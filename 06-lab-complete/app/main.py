from __future__ import annotations

import json
import logging
import signal
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
import redis
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import build_api_key_dependency
from app.chat_service import ChatService
from app.config import Settings, settings as default_settings
from app.cost_guard import RedisCostGuard
from app.gemini_client import build_llm
from app.rate_limiter import RedisRateLimiter
from app.web_ui import CHAT_PAGE_HTML, normalize_nickname


logger = logging.getLogger("day12.part6")


def configure_logging(log_level: str):
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(message)s",
        force=True,
    )


def log_event(event: str, **fields):
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    logger.info(json.dumps(payload, ensure_ascii=True))


def configure_tracing(app_settings: Settings):
    current_provider = trace.get_tracer_provider()
    if not isinstance(current_provider, TracerProvider):
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": app_settings.otel_service_name,
                    "service.version": app_settings.app_version,
                    "deployment.environment": app_settings.environment,
                }
            )
        )
        if app_settings.otel_exporter_otlp_endpoint:
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=app_settings.otel_exporter_otlp_endpoint)
                )
            )
        elif app_settings.otel_exporter_console:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
    return trace.get_tracer(app_settings.otel_service_name)


class AskRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=100)
    question: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    user_id: str
    question: str
    answer: str
    history_length: int
    served_by: str
    model: str
    timestamp: str
    usage: dict[str, float | int]


class WebAskRequest(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=40)
    question: str = Field(..., min_length=1, max_length=2000)

    @field_validator("nickname")
    @classmethod
    def nickname_must_normalize(cls, value: str) -> str:
        normalized = normalize_nickname(value)
        if not normalized:
            raise ValueError("Nickname is required")
        return value


def create_redis_client(redis_url: str):
    return redis.from_url(redis_url, decode_responses=True)


def load_history(redis_client, user_id: str) -> list[dict]:
    raw_value = redis_client.get(f"history:{user_id}")
    if not raw_value:
        return []
    return json.loads(raw_value)


def save_history(
    redis_client,
    user_id: str,
    history: list[dict],
    *,
    ttl_seconds: int,
):
    redis_client.setex(f"history:{user_id}", ttl_seconds, json.dumps(history))


def create_app(
    *,
    settings: Settings | None = None,
    redis_client=None,
    llm_func=None,
) -> FastAPI:
    app_settings = settings or default_settings
    configure_logging(app_settings.log_level)
    redis_conn = redis_client or create_redis_client(app_settings.redis_url)
    llm = llm_func or build_llm(app_settings)
    tracer = configure_tracing(app_settings)
    rate_limiter = RedisRateLimiter(
        redis_conn,
        max_requests=app_settings.rate_limit_per_minute,
        window_seconds=60,
    )
    cost_guard = RedisCostGuard(
        redis_conn,
        monthly_budget_usd=app_settings.monthly_budget_usd,
    )
    verify_api_key = build_api_key_dependency(app_settings.agent_api_key)
    start_time = time.time()
    metrics_registry = CollectorRegistry()
    http_requests_total = Counter(
        "agent_http_requests_total",
        "Total HTTP requests served by the agent",
        ["method", "path", "status"],
        registry=metrics_registry,
    )
    http_request_duration = Histogram(
        "agent_http_request_duration_seconds",
        "Request latency in seconds",
        ["method", "path"],
        registry=metrics_registry,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ready = False
        try:
            redis_conn.ping()
            app.state.ready = True
            log_event(
                "startup",
                app=app_settings.app_name,
                version=app_settings.app_version,
                environment=app_settings.environment,
                instance_id=app_settings.instance_id,
            )
        except Exception as exc:
            app.state.ready = False
            log_event("startup_failed", error=str(exc))
        yield
        app.state.ready = False
        log_event("shutdown", instance_id=app_settings.instance_id)

    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if app_settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = app_settings
    app.state.redis = redis_conn
    app.state.rate_limiter = rate_limiter
    app.state.cost_guard = cost_guard
    chat_service = ChatService(
        settings=app_settings,
        redis_client=redis_conn,
        rate_limiter=rate_limiter,
        cost_guard=cost_guard,
        llm_func=llm,
        load_history=load_history,
        save_history=save_history,
    )
    app.state.chat_service = chat_service

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
        started_at = time.time()
        with tracer.start_as_current_span(f"{request.method} {request.url.path}") as span:
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.route", request.url.path)
            try:
                response: Response = await call_next(request)
            except Exception as exc:
                span.record_exception(exc)
                span.set_attribute("http.status_code", 500)
                log_event("request_failed", path=request.url.path, method=request.method)
                raise

            duration_seconds = time.time() - started_at
            duration_ms = round(duration_seconds * 1000, 1)
            span.set_attribute("http.status_code", response.status_code)
            trace_id = format(span.get_span_context().trace_id, "032x")
            response.headers["X-Trace-Id"] = trace_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Cache-Control"] = "no-store"
            http_requests_total.labels(
                method=request.method,
                path=request.url.path,
                status=str(response.status_code),
            ).inc()
            http_request_duration.labels(
                method=request.method,
                path=request.url.path,
            ).observe(duration_seconds)
            log_event(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
                trace_id=trace_id,
            )
            return response

    @app.get("/", response_class=HTMLResponse)
    def root():
        return HTMLResponse(CHAT_PAGE_HTML)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "instance_id": app_settings.instance_id,
            "uptime_seconds": round(time.time() - start_time, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/ready")
    def ready():
        try:
            redis_conn.ping()
        except Exception as exc:
            app.state.ready = False
            raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")

        if not app.state.ready:
            raise HTTPException(status_code=503, detail="Application is not ready")

        return {"ready": True, "instance_id": app_settings.instance_id}

    @app.get("/metrics")
    def metrics():
        if not app_settings.prometheus_enabled:
            raise HTTPException(status_code=404, detail="Prometheus metrics disabled")
        return Response(
            generate_latest(metrics_registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.post("/ask", response_model=AskResponse)
    async def ask_agent(
        body: AskRequest,
        _api_key: str = Depends(verify_api_key),
    ):
        result = chat_service.ask(user_id=body.user_id, question=body.question)
        log_event(
            "agent_answered",
            user_id=result["user_id"],
            history_length=result["history_length"],
            context_messages_used=result["usage"]["context_messages_used"],
            instance_id=app_settings.instance_id,
        )

        return AskResponse(
            user_id=result["user_id"],
            question=result["question"],
            answer=result["answer"],
            history_length=result["history_length"],
            served_by=app_settings.instance_id,
            model=app_settings.llm_model,
            timestamp=datetime.now(timezone.utc).isoformat(),
            usage=result["usage"],
        )

    @app.post("/web/ask", response_model=AskResponse)
    async def ask_from_web(body: WebAskRequest):
        user_id = normalize_nickname(body.nickname)
        result = chat_service.ask(user_id=user_id, question=body.question)
        log_event(
            "web_agent_answered",
            user_id=result["user_id"],
            history_length=result["history_length"],
            context_messages_used=result["usage"]["context_messages_used"],
            instance_id=app_settings.instance_id,
        )
        return AskResponse(
            user_id=result["user_id"],
            question=result["question"],
            answer=result["answer"],
            history_length=result["history_length"],
            served_by=app_settings.instance_id,
            model=app_settings.llm_model,
            timestamp=datetime.now(timezone.utc).isoformat(),
            usage=result["usage"],
        )

    return app


app = create_app()


def _handle_signal(signum, _frame):
    log_event("signal", signum=signum)


signal.signal(signal.SIGTERM, _handle_signal)


if __name__ == "__main__":
    current_settings = default_settings
    uvicorn.run(
        "app.main:app",
        host=current_settings.host,
        port=current_settings.port,
        reload=current_settings.debug,
        timeout_graceful_shutdown=30,
    )
