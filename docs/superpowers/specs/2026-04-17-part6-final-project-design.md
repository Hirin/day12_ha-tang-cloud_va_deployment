# Part 6 Final Project Design

**Goal:** Bring `06-lab-complete` in line with the Day 12 Part 6 checklist by making the app genuinely stateless, production-shaped, and ready for CI/CD-oriented deployment on Railway first, with Render as fallback.

**Scope:**
- Keep the final deliverable inside `06-lab-complete/`.
- Preserve FastAPI + Docker deployment shape already used in the lab.
- Prefer a local stack with `nginx + multiple agent instances + redis`.
- Prefer a single web service plus managed Redis on Railway/Render for cloud deployment.

**Architecture:**
- Split the current oversized `app/main.py` responsibilities into focused modules for config, auth, rate limiting, and cost guard.
- Store conversation history, rate-limit windows, and monthly budget usage in Redis so any instance can serve the same user.
- Expose the app behind Nginx locally to demonstrate load balancing, while keeping cloud deploy config simple and platform-compatible.

**Core Behaviors:**
- `POST /ask` requires `X-API-Key`.
- Each request carries `user_id` and `question`.
- Conversation history is loaded from Redis, appended on each turn, and trimmed to a bounded size.
- Rate limiting is enforced at 10 requests/minute per user.
- Cost guard enforces a monthly per-user budget, default `$10`.
- `GET /health` reports liveness.
- `GET /ready` fails when Redis is unavailable or startup is incomplete.
- Structured JSON logs are emitted for startup, requests, and shutdown.
- SIGTERM should trigger graceful shutdown through Uvicorn/FastAPI lifespan handling.

**Local Deployment Model:**
- `agent` service built from the production Dockerfile.
- `redis` service for shared state.
- `nginx` service as the exposed entrypoint.
- `docker compose up --scale agent=3` should demonstrate stateless behavior across instances.

**Cloud Deployment Model:**
- Railway is the primary target.
- Render remains the fallback.
- CI/CD is the only explicit next step after implementation; no extra monitoring/observability work is in scope.
