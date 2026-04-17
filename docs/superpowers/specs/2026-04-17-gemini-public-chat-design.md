# Gemini Public Chat Design

**Goal:** Replace the mock LLM in `06-lab-complete` with a Gemini-backed provider while adding a simple public chat UI at `/` that keeps provider credentials on the backend.

## Scope

- Keep the work inside `06-lab-complete/`.
- Keep the existing FastAPI app, Redis-backed state, Docker deploy shape, and protected `POST /ask` endpoint.
- Add a minimal web chat page served directly from FastAPI at `/`.
- Add a public backend route for the chat page so browser users do not need to know `X-API-Key`.
- Switch model calls from mock responses to Gemini using server-side environment variables.
- Keep the implementation simple enough to deploy on Render or Railway without a separate frontend build step.

## Non-Goals

- No separate frontend app or SPA build pipeline.
- No user authentication system.
- No multi-room chat or file upload support.
- No provider key exposure in the browser.
- No removal of the existing API-level protection on `POST /ask`.

## Architecture

The app remains a single FastAPI service with Redis as the shared state store. The backend will gain a provider adapter module responsible for Gemini API requests, response parsing, timeout handling, and provider-level error normalization. `app/main.py` will continue to compose the application, but the actual model invocation logic will move behind a dedicated interface so tests can inject a fake LLM without hitting Gemini.

The root route `/` will serve a small HTML page with embedded CSS and JavaScript. The page will contain a nickname field, question input, submit button, loading state, and a simple chat transcript. The browser will call a new public endpoint such as `POST /web/ask`. That route will validate `nickname` and `question`, convert the nickname into the internal `user_id`, reuse the existing Redis-backed history, rate limit, and budget guard flow, then return JSON that the UI can render.

The existing `POST /ask` route will remain protected by `X-API-Key` for admin, scripting, and testing use. The new web route will be the only public route for chat interactions. This separation keeps the API protection intact while allowing a browser-based demo to remain usable for anyone with the public link.

## Configuration

The current `OPENAI_API_KEY` usage will be replaced by Gemini-specific environment variables:

- `GEMINI_API_KEY` for the provider credential
- `LLM_PROVIDER=gemini` to make the provider choice explicit
- `LLM_MODEL=gemini-3.1-flash-lite-preview` as the requested default model

`AGENT_API_KEY` remains required for the protected `POST /ask` route. The public chat page will not use or expose it. Existing Redis, rate-limit, budget, logging, and readiness settings remain in place.

If the app runs in production with `LLM_PROVIDER=gemini`, startup should fail fast when `GEMINI_API_KEY` is missing. That makes deploy issues visible immediately instead of failing only after the first user request.

## Routes And Data Flow

### `GET /`

- Returns the minimal public chat HTML page.
- Does not require `X-API-Key`.
- Uses plain JavaScript fetch requests to talk to the backend.

### `POST /web/ask`

- Public route for the browser UI.
- Request body:
  - `nickname`
  - `question`
- Validation rules:
  - nickname required
  - question required
  - nickname normalized into the internal `user_id`
- Processing flow:
  - load Redis history using normalized nickname
  - enforce rate limit for that nickname
  - enforce monthly budget for that nickname
  - call Gemini through the provider adapter
  - append the assistant reply to history
  - return response JSON for the UI

### `POST /ask`

- Remains protected by `X-API-Key`
- Continues serving API/admin/testing traffic
- Reuses the same provider adapter and Redis-backed conversation flow

## User Experience

The page at `/` is intentionally minimal:

- one nickname input
- one message input
- one send button
- one transcript area showing user messages and assistant replies
- one inline loading indicator
- one small error area

The page should work on both desktop and mobile without introducing a frontend build chain. The user enters a nickname once and then keeps chatting. If they change nickname, the backend treats them as a different user because the nickname maps directly to the internal `user_id`.

The page should show concise, plain-language error messages:

- missing nickname
- missing question
- rate limit reached
- budget exceeded
- temporary provider failure

## Gemini Integration

The Gemini adapter is responsible for:

- reading provider config from environment-backed settings
- building the request payload for `gemini-3.1-flash-lite-preview`
- converting the stored conversation history into provider input
- handling provider timeouts and non-2xx responses
- returning normalized output text and usage information

The backend should continue to support dependency injection for tests by passing a fake LLM function or fake provider implementation into `create_app()`. The concrete Gemini client should only be used in normal runtime paths.

## Cost And Usage Handling

The current budget guard should remain in place, but usage recording should no longer rely only on word-count estimation when the provider supplies better usage data. The design goal is:

- prefer provider-reported usage when available
- fall back to the current estimate only if provider usage is unavailable

This keeps the behavior compatible while improving budget accuracy once Gemini responses are live.

## Error Handling

- Invalid browser input returns a user-readable `400` or `422`.
- Rate limiting remains `429`.
- Budget exhaustion remains `402`.
- Provider timeout or upstream failure returns `502` or `503` with a short friendly message.
- The UI must not surface raw stack traces or provider secrets.
- Structured logs should record provider failures with enough context to debug without logging secrets.

## Testing

The test suite should expand to cover:

- `GET /` returns HTML successfully
- `POST /web/ask` works without `X-API-Key`
- `POST /ask` still rejects missing `X-API-Key`
- public web flow still persists history by nickname
- public web flow still respects rate limit and budget guard
- provider adapter can be replaced with a fake implementation in tests
- provider failures map to stable HTTP errors and UI-safe messages

No test should depend on a real Gemini API key or live network access.

## Deployment Impact

The current Docker, Railway, and Render setup remains valid. The main deployment changes are environment variables:

- add `LLM_PROVIDER=gemini`
- add `GEMINI_API_KEY`
- set `LLM_MODEL=gemini-3.1-flash-lite-preview`

The public UI will make the service accessible to anyone with the app link, while the raw API route `POST /ask` remains protected by `AGENT_API_KEY`.

## Acceptance Criteria

- Opening `/` shows a working browser chat UI
- Browser users can chat without knowing any backend API key
- `POST /ask` remains protected by `X-API-Key`
- Gemini requests are executed only from the backend
- Nickname-based history persists through Redis
- Existing rate limit and budget controls still apply
- Render or Railway deployment only needs environment variable updates, not a new frontend service
