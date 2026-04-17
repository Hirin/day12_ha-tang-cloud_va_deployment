# Gemini Public Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mock LLM in `06-lab-complete` with a Gemini-backed provider and ship a simple public chat UI at `/` while keeping `POST /ask` protected by `X-API-Key`.

**Architecture:** Keep FastAPI as the single service and Redis as the shared state store. Add three focused modules: one for shared chat-turn orchestration, one for the Gemini REST client, and one for the minimal browser UI. Both `POST /ask` and `POST /web/ask` will reuse the same chat service so history, rate limiting, budget guard, and logging stay consistent.

**Tech Stack:** Python 3.11, FastAPI, Redis, httpx, Prometheus, plain HTML/CSS/JavaScript, pytest

---

## File Structure

- Create: `06-lab-complete/app/chat_service.py`  
  Responsibility: shared chat-turn pipeline, normalized LLM reply structure, provider-safe error mapping, usage fallback logic.
- Create: `06-lab-complete/app/gemini_client.py`  
  Responsibility: Gemini REST API calls over `httpx`, response parsing, timeout handling, provider selection.
- Create: `06-lab-complete/app/web_ui.py`  
  Responsibility: public chat HTML string and nickname normalization helper.
- Modify: `06-lab-complete/app/config.py`  
  Responsibility: Gemini-specific environment variables and production validation.
- Modify: `06-lab-complete/app/main.py`  
  Responsibility: wire the new modules, serve `/`, keep `/ask`, add `/web/ask`.
- Modify: `06-lab-complete/tests/test_app.py`  
  Responsibility: regression tests for public UI, protected API preservation, provider usage fallback, provider failure mapping.
- Modify: `06-lab-complete/.env.example`  
  Responsibility: document Gemini env vars for local and cloud deploys.
- Modify: `06-lab-complete/README.md`  
  Responsibility: explain public `/` UI, protected `/ask`, and Render env setup for Gemini.
- Modify: `06-lab-complete/render.yaml`  
  Responsibility: seed Gemini-related env vars in the Render blueprint.

### Task 1: Public UI And Shared Chat Flow

**Files:**
- Create: `06-lab-complete/app/chat_service.py`
- Create: `06-lab-complete/app/web_ui.py`
- Modify: `06-lab-complete/app/main.py`
- Modify: `06-lab-complete/tests/test_app.py`
- Test: `06-lab-complete/tests/test_app.py`

- [ ] **Step 1: Write the failing tests for `/` and `/web/ask`**

Add these tests to `06-lab-complete/tests/test_app.py` below the existing readiness test:

```python
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
```

- [ ] **Step 2: Run the focused test file and verify it fails for the right reason**

Run:

```bash
cd /mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment/06-lab-complete
python -m pytest tests/test_app.py -q
```

Expected:

- `test_root_serves_public_chat_html` fails because `/` still returns JSON
- `test_web_ask_works_without_api_key_and_persists_history` fails with `404 Not Found`
- `test_web_ask_rejects_blank_nickname` fails with `404 Not Found`

- [ ] **Step 3: Implement the minimal public UI and shared chat orchestration**

Create `06-lab-complete/app/chat_service.py` with a small shared service that both routes can call:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException


@dataclass
class LLMReply:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderUnavailableError(RuntimeError):
    pass


class ChatService:
    def __init__(
        self,
        *,
        settings,
        redis_client,
        rate_limiter,
        cost_guard,
        llm_func: Callable[[str, list[dict]], str | LLMReply],
        load_history,
        save_history,
    ):
        self.settings = settings
        self.redis = redis_client
        self.rate_limiter = rate_limiter
        self.cost_guard = cost_guard
        self.llm_func = llm_func
        self.load_history = load_history
        self.save_history = save_history

    def ask(self, *, user_id: str, question: str) -> dict:
        self.cost_guard.check_budget(user_id)
        rate_info = self.rate_limiter.check(user_id)
        history = self.load_history(self.redis, user_id)

        user_message = {
            "role": "user",
            "content": question,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        history_with_question = history + [user_message]
        llm_context = history_with_question[-self.settings.model_context_messages :]

        raw_reply = self.llm_func(question, llm_context)
        reply = raw_reply if isinstance(raw_reply, LLMReply) else LLMReply(text=str(raw_reply))

        assistant_message = {
            "role": "assistant",
            "content": reply.text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        updated_history = (history_with_question + [assistant_message])[
            -self.settings.conversation_history_limit :
        ]
        self.save_history(
            self.redis,
            user_id,
            updated_history,
            ttl_seconds=self.settings.conversation_ttl_seconds,
        )

        input_tokens = reply.input_tokens or len(question.split()) * 2
        output_tokens = reply.output_tokens or len(reply.text.split()) * 2
        usage = self.cost_guard.record_usage(
            user_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        usage.update(rate_info)
        usage["context_messages_used"] = len(llm_context)

        return {
            "user_id": user_id,
            "question": question,
            "answer": reply.text,
            "history_length": len(updated_history),
            "usage": usage,
        }
```

Create `06-lab-complete/app/web_ui.py` with the HTML page and nickname normalization:

```python
from __future__ import annotations

import re


CHAT_PAGE_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Production AI Agent</title>
    <style>
      :root { color-scheme: light; }
      body { margin: 0; font-family: Georgia, serif; background: linear-gradient(160deg, #f4efe6, #dfe9f3); color: #1b1d1f; }
      main { max-width: 760px; margin: 0 auto; padding: 40px 20px 64px; }
      h1 { margin: 0 0 12px; font-size: 2.2rem; }
      p { margin: 0 0 20px; line-height: 1.5; }
      form, .chat-log { background: rgba(255,255,255,0.88); border: 1px solid rgba(27,29,31,0.12); border-radius: 16px; }
      form { padding: 16px; display: grid; gap: 12px; }
      label { display: grid; gap: 6px; font-weight: 600; }
      input, textarea, button { font: inherit; }
      input, textarea { width: 100%; padding: 12px; border: 1px solid #c3ccd5; border-radius: 10px; box-sizing: border-box; }
      textarea { min-height: 110px; resize: vertical; }
      button { width: fit-content; padding: 12px 18px; border: 0; border-radius: 999px; background: #153b50; color: white; cursor: pointer; }
      .chat-log { margin-top: 18px; padding: 16px; min-height: 220px; }
      .message { padding: 12px 14px; border-radius: 12px; margin-bottom: 10px; }
      .message.user { background: #edf4ff; }
      .message.bot { background: #f7f2e8; }
      .status { min-height: 24px; margin-top: 12px; color: #6a2c2c; }
    </style>
  </head>
  <body>
    <main>
      <h1>Public AI Chat</h1>
      <p>Enter a nickname, ask a question, and the backend will keep your conversation history in Redis.</p>
      <form id="chat-form">
        <label>
          Nickname
          <input id="nickname" name="nickname" maxlength="40" required />
        </label>
        <label>
          Question
          <textarea id="question" name="question" maxlength="2000" required></textarea>
        </label>
        <button type="submit">Send</button>
      </form>
      <div id="status" class="status" aria-live="polite"></div>
      <section id="chat-log" class="chat-log"></section>
    </main>
    <script>
      const form = document.getElementById("chat-form");
      const chatLog = document.getElementById("chat-log");
      const statusEl = document.getElementById("status");

      function appendMessage(role, content) {
        const item = document.createElement("article");
        item.className = `message ${role}`;
        item.textContent = content;
        chatLog.appendChild(item);
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        statusEl.textContent = "Thinking...";

        const nickname = document.getElementById("nickname").value;
        const question = document.getElementById("question").value;
        appendMessage("user", question);

        try {
          const response = await fetch("/web/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nickname, question }),
          });
          const payload = await response.json();
          if (!response.ok) {
            throw new Error(payload.detail || "The bot is temporarily unavailable.");
          }
          appendMessage("bot", payload.answer);
          form.reset();
          document.getElementById("nickname").value = nickname;
          statusEl.textContent = "";
        } catch (error) {
          statusEl.textContent = error.message;
        }
      });
    </script>
  </body>
</html>
"""


def normalize_nickname(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    return cleaned[:40]
```

Modify `06-lab-complete/app/main.py` to use the new files and expose the new route:

```python
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from app.chat_service import ChatService
from app.web_ui import CHAT_PAGE_HTML, normalize_nickname
```

```python
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
```

```python
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
```

```python
@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(CHAT_PAGE_HTML)


@app.post("/web/ask", response_model=AskResponse)
async def ask_from_web(body: WebAskRequest):
    result = chat_service.ask(
        user_id=normalize_nickname(body.nickname),
        question=body.question,
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
```

Replace the body of `ask_agent()` so it also uses `chat_service.ask(...)` instead of duplicating the conversation logic:

```python
result = chat_service.ask(user_id=body.user_id, question=body.question)
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
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
cd /mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment/06-lab-complete
python -m pytest tests/test_app.py::test_root_serves_public_chat_html tests/test_app.py::test_web_ask_works_without_api_key_and_persists_history tests/test_app.py::test_web_ask_rejects_blank_nickname tests/test_app.py::test_ask_requires_api_key -q
```

Expected:

- `4 passed`

- [ ] **Step 5: Commit the public UI and shared flow**

Run:

```bash
cd /mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment
git add 06-lab-complete/app/chat_service.py 06-lab-complete/app/web_ui.py 06-lab-complete/app/main.py 06-lab-complete/tests/test_app.py
git commit -m "feat: add public chat ui and shared chat service"
```

### Task 2: Gemini Provider Integration And Provider-Safe Errors

**Files:**
- Create: `06-lab-complete/app/gemini_client.py`
- Modify: `06-lab-complete/app/config.py`
- Modify: `06-lab-complete/app/chat_service.py`
- Modify: `06-lab-complete/app/main.py`
- Modify: `06-lab-complete/tests/test_app.py`
- Test: `06-lab-complete/tests/test_app.py`

- [ ] **Step 1: Write the failing tests for provider usage, provider failure, and production validation**

Append these tests to `06-lab-complete/tests/test_app.py`:

```python
from app.chat_service import LLMReply
```

```python
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
        llm_func=lambda question, history: (_ for _ in ()).throw(RuntimeError("provider down"))
    ) as client:
        response = client.post(
            "/web/ask",
            json={"nickname": "alice", "question": "hello"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "The bot is temporarily unavailable. Please try again."


def test_production_requires_gemini_key_when_provider_is_gemini():
    with pytest.raises(ValueError, match="GEMINI_API_KEY must be set when LLM_PROVIDER=gemini in production"):
        Settings(
            environment="production",
            debug=False,
            agent_api_key="test-key",
            llm_provider="gemini",
            gemini_api_key="",
        )
```

Also update the `build_client()` helper signature near the top of the file so later tasks can inject a custom LLM:

```python
def build_client(
    *,
    redis_client: FakeRedis | None = None,
    rate_limit_per_minute: int = 10,
    monthly_budget_usd: float = 10.0,
    llm_func=None,
):
```

and pass it through to `create_app()`:

```python
    app = create_app(
        settings=settings,
        redis_client=redis_client or FakeRedis(),
        llm_func=llm_func or (lambda question, history: f"echo:{question}|turns:{len(history)}"),
    )
```

- [ ] **Step 2: Run the targeted tests and confirm they fail**

Run:

```bash
cd /mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment/06-lab-complete
python -m pytest tests/test_app.py::test_provider_usage_tokens_are_preferred_when_available tests/test_app.py::test_web_ask_maps_provider_failures_to_503 tests/test_app.py::test_production_requires_gemini_key_when_provider_is_gemini -q
```

Expected:

- `test_provider_usage_tokens_are_preferred_when_available` fails because `LLMReply` is not imported or token usage is ignored
- `test_web_ask_maps_provider_failures_to_503` fails because runtime errors still escape as `500`
- `test_production_requires_gemini_key_when_provider_is_gemini` fails because the config does not yet know `llm_provider` or `gemini_api_key`

- [ ] **Step 3: Implement the Gemini client, config, and provider-safe error mapping**

Create `06-lab-complete/app/gemini_client.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.chat_service import LLMReply, ProviderUnavailableError
from utils.mock_llm import ask as mock_llm_ask


@dataclass
class GeminiClient:
    api_key: str
    model: str
    timeout_seconds: float
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    def __call__(self, question: str, history: list[dict]) -> LLMReply:
        contents = []
        for item in history:
            role = "model" if item["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": item["content"]}]})

        url = f"{self.base_url}/models/{self.model}:generateContent"
        try:
            response = httpx.post(
                url,
                params={"key": self.api_key},
                json={"contents": contents or [{"role": "user", "parts": [{"text": question}]}]},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Gemini request failed") from exc

        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise ProviderUnavailableError("Gemini returned no candidates")

        text = "".join(
            part.get("text", "")
            for part in candidates[0].get("content", {}).get("parts", [])
        ).strip()
        if not text:
            raise ProviderUnavailableError("Gemini returned an empty response")

        usage = data.get("usageMetadata", {})
        return LLMReply(
            text=text,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )


def build_llm(settings):
    if settings.llm_provider == "mock":
        return lambda question, history: mock_llm_ask(question)

    if settings.llm_provider == "gemini":
        return GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.gemini_timeout_seconds,
            base_url=settings.gemini_api_base_url,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
```

Update `06-lab-complete/app/config.py` by replacing the old OpenAI-only fields with explicit provider config:

```python
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gemini-3.1-flash-lite-preview"))
    gemini_api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_API_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        )
    )
    gemini_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))
    )
```

Then extend `__post_init__()`:

```python
        self.llm_provider = self.llm_provider.strip().lower()
        if self.llm_provider not in {"mock", "gemini"}:
            raise ValueError("LLM_PROVIDER must be one of: mock, gemini")

        if self.environment == "production" and self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY must be set when LLM_PROVIDER=gemini in production")
```

Update `06-lab-complete/app/chat_service.py` so provider failures become stable HTTP errors and provider usage wins when present:

```python
from fastapi import HTTPException
```

```python
        try:
            raw_reply = self.llm_func(question, llm_context)
        except ProviderUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail="The bot is temporarily unavailable. Please try again.",
            ) from exc
```

Keep the usage fallback exactly like this:

```python
        input_tokens = reply.input_tokens or len(question.split()) * 2
        output_tokens = reply.output_tokens or len(reply.text.split()) * 2
```

Update `06-lab-complete/app/main.py` to build the default LLM from settings:

```python
from app.gemini_client import build_llm
```

```python
def default_llm(question: str, history: list[dict]):
    return build_llm(default_settings)(question, history)
```

and in `create_app()`:

```python
    llm = llm_func or build_llm(app_settings)
```

- [ ] **Step 4: Run the targeted provider tests and then the full test file**

Run:

```bash
cd /mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment/06-lab-complete
python -m pytest tests/test_app.py::test_provider_usage_tokens_are_preferred_when_available tests/test_app.py::test_web_ask_maps_provider_failures_to_503 tests/test_app.py::test_production_requires_gemini_key_when_provider_is_gemini -q
python -m pytest tests/test_app.py -q
```

Expected:

- targeted run: `3 passed`
- full run: all tests pass

- [ ] **Step 5: Commit the Gemini provider work**

Run:

```bash
cd /mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment
git add 06-lab-complete/app/gemini_client.py 06-lab-complete/app/config.py 06-lab-complete/app/chat_service.py 06-lab-complete/app/main.py 06-lab-complete/tests/test_app.py
git commit -m "feat: integrate Gemini provider for public chat"
```

### Task 3: Deployment Docs And Render Configuration

**Files:**
- Modify: `06-lab-complete/.env.example`
- Modify: `06-lab-complete/README.md`
- Modify: `06-lab-complete/render.yaml`
- Test: `06-lab-complete/tests/test_app.py`

- [ ] **Step 1: Add the failing deployment-facing assertions in the docs and env template**

Update `06-lab-complete/.env.example` so the provider section becomes:

```env
LLM_PROVIDER=mock
GEMINI_API_KEY=
LLM_MODEL=gemini-3.1-flash-lite-preview
GEMINI_API_BASE_URL=https://generativelanguage.googleapis.com/v1beta
GEMINI_TIMEOUT_SECONDS=20
```

Update `06-lab-complete/render.yaml` so the web service gets Gemini defaults:

```yaml
      - key: LLM_PROVIDER
        value: gemini
      - key: GEMINI_API_KEY
        sync: false
      - key: LLM_MODEL
        value: gemini-3.1-flash-lite-preview
```

Update `06-lab-complete/README.md` in three places:

1. In the feature list, replace the old mock-only wording with:

```md
- Public web chat UI at `GET /`
- Protected API chat at `POST /ask`
- Gemini provider support through backend-only `GEMINI_API_KEY`
```

2. In the quick test section, add:

```bash
curl http://localhost:8080/
curl -X POST http://localhost:8080/web/ask \
  -H "Content-Type: application/json" \
  -d '{"nickname":"alice","question":"What is deployment?"}'
```

3. In the Render deploy section, add the exact env vars:

```env
ENVIRONMENT=production
AGENT_API_KEY=lab12-test
REDIS_URL=redis://...
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-key
LLM_MODEL=gemini-3.1-flash-lite-preview
RATE_LIMIT_PER_MINUTE=10
MONTHLY_BUDGET_USD=10.0
LOG_LEVEL=INFO
```

- [ ] **Step 2: Run a verification pass on tests and the readiness checker**

Run:

```bash
cd /mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment/06-lab-complete
python -m pytest tests/test_app.py -q
python check_production_ready.py
```

Expected:

- pytest reports all tests passing
- readiness checker still exits successfully

- [ ] **Step 3: Commit the docs and Render updates**

Run:

```bash
cd /mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment
git add 06-lab-complete/.env.example 06-lab-complete/README.md 06-lab-complete/render.yaml
git commit -m "docs: document Gemini deploy and public chat ui"
```
