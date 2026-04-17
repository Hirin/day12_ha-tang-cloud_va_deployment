from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.chat_service import LLMReply, ProviderUnavailableError
from app.config import Settings
from app.main import create_app


class FakeRedis:
    def __init__(self, *, fail_ping: bool = False):
        self.fail_ping = fail_ping
        self.store: dict[str, str] = {}

    def ping(self):
        if self.fail_ping:
            raise RuntimeError("redis unavailable")
        return True

    def get(self, key: str):
        return self.store.get(key)

    def setex(self, key: str, _ttl_seconds: int, value: str):
        self.store[key] = value
        return True

    def delete(self, key: str):
        self.store.pop(key, None)
        return 1


def build_client(
    *,
    redis_client: FakeRedis | None = None,
    rate_limit_per_minute: int = 10,
    monthly_budget_usd: float = 10.0,
    llm_func=None,
):
    settings = Settings(
        environment="test",
        debug=False,
        agent_api_key="test-key",
        rate_limit_per_minute=rate_limit_per_minute,
        monthly_budget_usd=monthly_budget_usd,
        redis_url="redis://fake:6379/0",
        conversation_ttl_seconds=3600,
        conversation_history_limit=6,
    )
    app = create_app(
        settings=settings,
        redis_client=redis_client or FakeRedis(),
        llm_func=llm_func or (lambda question, history: f"echo:{question}|turns:{len(history)}"),
    )
    return TestClient(app)


def auth_headers():
    return {"X-API-Key": "test-key", "Content-Type": "application/json"}


def test_ask_requires_api_key():
    with build_client() as client:
        response = client.post(
            "/ask",
            json={"user_id": "alice", "question": "hello"},
        )

    assert response.status_code == 401


def test_ready_returns_503_when_redis_is_unavailable():
    with build_client(redis_client=FakeRedis(fail_ping=True)) as client:
        response = client.get("/ready")

    assert response.status_code == 503


def test_root_serves_public_chat_html():
    with build_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>Production AI Agent</title>" in response.text
    assert 'name="nickname"' in response.text
    assert 'id="chat-form"' in response.text


def test_web_ask_works_without_api_key_and_persists_history():
    with build_client() as client:
        first = client.post(
            "/web/ask",
            json={"nickname": "Alice", "question": "hello"},
        )
        second = client.post(
            "/web/ask",
            json={"nickname": "Alice", "question": "again"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["user_id"] == "alice"
    assert second.json()["history_length"] == 4
    assert second.json()["answer"] == "echo:again|turns:3"


def test_web_ask_rejects_blank_nickname():
    with build_client() as client:
        response = client.post(
            "/web/ask",
            json={"nickname": "   ", "question": "hello"},
        )

    assert response.status_code == 422


def test_conversation_history_is_persisted_per_user():
    with build_client() as client:
        first = client.post(
            "/ask",
            headers=auth_headers(),
            json={"user_id": "alice", "question": "hello"},
        )
        second = client.post(
            "/ask",
            headers=auth_headers(),
            json={"user_id": "alice", "question": "again"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "X-Trace-Id" in first.headers
    assert first.json()["history_length"] == 2
    assert second.json()["history_length"] == 4
    assert second.json()["answer"] == "echo:again|turns:3"


def test_rate_limit_is_enforced_per_user():
    with build_client(rate_limit_per_minute=10) as client:
        for attempt in range(10):
            response = client.post(
                "/ask",
                headers=auth_headers(),
                json={"user_id": "alice", "question": f"req-{attempt}"},
            )
            assert response.status_code == 200

        limited = client.post(
            "/ask",
            headers=auth_headers(),
            json={"user_id": "alice", "question": "req-11"},
        )

    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"


def test_monthly_budget_is_enforced_per_user():
    with build_client(monthly_budget_usd=0.000002) as client:
        first = client.post(
            "/ask",
            headers=auth_headers(),
            json={"user_id": "alice", "question": "one two three four five"},
        )
        second = client.post(
            "/ask",
            headers=auth_headers(),
            json={"user_id": "alice", "question": "one two three four five"},
        )

    assert first.status_code == 200
    assert second.status_code == 402
    detail = second.json()["detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    assert detail["error"] == "Monthly budget exceeded"


def test_metrics_endpoint_exposes_prometheus_metrics():
    with build_client() as client:
        client.get("/health")
        client.post(
            "/ask",
            headers=auth_headers(),
            json={"user_id": "alice", "question": "hello"},
        )
        metrics = client.get("/metrics")

    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    assert "agent_http_requests_total" in metrics.text


def test_provider_usage_tokens_are_preferred_when_available():
    settings = Settings(
        environment="test",
        debug=False,
        agent_api_key="test-key",
        rate_limit_per_minute=10,
        monthly_budget_usd=10.0,
        redis_url="redis://fake:6379/0",
        conversation_ttl_seconds=3600,
        conversation_history_limit=6,
    )
    app = create_app(
        settings=settings,
        redis_client=FakeRedis(),
        llm_func=lambda question, history: LLMReply(
            text="provider reply",
            input_tokens=50,
            output_tokens=25,
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/web/ask",
            json={"nickname": "alice", "question": "hello"},
        )

    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["request_cost_usd"] == 2.2e-05


def test_web_ask_maps_provider_failures_to_503():
    with build_client(
        llm_func=lambda question, history: (_ for _ in ()).throw(
            ProviderUnavailableError("provider down")
        )
    ) as client:
        response = client.post(
            "/web/ask",
            json={"nickname": "alice", "question": "hello"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "The bot is temporarily unavailable. Please try again."


def test_production_requires_gemini_key_when_provider_is_gemini():
    with pytest.raises(
        ValueError,
        match="GEMINI_API_KEY must be set when LLM_PROVIDER=gemini in production",
    ):
        Settings(
            environment="production",
            debug=False,
            agent_api_key="test-key",
            llm_provider="gemini",
            gemini_api_key="",
        )
