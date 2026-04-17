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
from pydantic import BaseModel, Field

from app.auth import build_api_key_dependency
from app.config import Settings, settings as default_settings
from app.cost_guard import RedisCostGuard
from app.rate_limiter import RedisRateLimiter
from utils.mock_llm import ask as mock_llm_ask


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


def create_redis_client(redis_url: str):
    return redis.from_url(redis_url, decode_responses=True)


def default_llm(question: str, _history: list[dict]) -> str:
    return mock_llm_ask(question)


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
    llm = llm_func or default_llm
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

    @app.get("/")
    def root():
        return {
            "app": app_settings.app_name,
            "version": app_settings.app_version,
            "environment": app_settings.environment,
            "instance_id": app_settings.instance_id,
        }

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
        cost_guard.check_budget(body.user_id)
        rate_info = rate_limiter.check(body.user_id)

        history = load_history(redis_conn, body.user_id)
        user_message = {
            "role": "user",
            "content": body.question,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        history_with_question = history + [user_message]
        llm_context = history_with_question[-app_settings.model_context_messages :]
        answer = llm(body.question, llm_context)
        assistant_message = {
            "role": "assistant",
            "content": answer,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        updated_history = (history_with_question + [assistant_message])[
            -app_settings.conversation_history_limit :
        ]
        save_history(
            redis_conn,
            body.user_id,
            updated_history,
            ttl_seconds=app_settings.conversation_ttl_seconds,
        )

        input_tokens = len(body.question.split()) * 2
        output_tokens = len(answer.split()) * 2
        usage = cost_guard.record_usage(
            body.user_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        usage.update(rate_info)
        usage["context_messages_used"] = len(llm_context)

        log_event(
            "agent_answered",
            user_id=body.user_id,
            history_length=len(updated_history),
            context_messages_used=len(llm_context),
            instance_id=app_settings.instance_id,
        )

        return AskResponse(
            user_id=body.user_id,
            question=body.question,
            answer=answer,
            history_length=len(updated_history),
            served_by=app_settings.instance_id,
            model=app_settings.llm_model,
            timestamp=datetime.now(timezone.utc).isoformat(),
            usage=usage,
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
