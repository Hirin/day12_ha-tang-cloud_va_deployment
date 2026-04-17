# Part 6 Final Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `06-lab-complete` into a production-shaped final project that matches the Day 12 Part 6 checklist and is ready for CI/CD plus Railway deployment.

**Architecture:** Keep FastAPI as the application entrypoint, move stateful concerns into Redis-backed helper modules, and run the local stack through Nginx to demonstrate load balancing. Cloud deployment remains single-service app plus Redis, with Railway first and Render fallback.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, Redis, Docker, Docker Compose, Nginx, pytest

---

### Task 1: Lock behavior with tests

**Files:**
- Create: `06-lab-complete/tests/test_app.py`
- Modify: `06-lab-complete/requirements.txt`

- [ ] Add failing API tests for auth, readiness, Redis-backed history, rate limiting, and monthly budget guard.
- [ ] Run the focused test file and confirm failures are due to missing or incorrect behavior.

### Task 2: Split production logic into focused modules

**Files:**
- Create: `06-lab-complete/app/__init__.py`
- Create: `06-lab-complete/app/auth.py`
- Create: `06-lab-complete/app/rate_limiter.py`
- Create: `06-lab-complete/app/cost_guard.py`
- Modify: `06-lab-complete/app/config.py`
- Modify: `06-lab-complete/app/main.py`

- [ ] Implement minimal Redis-backed modules to satisfy the failing tests.
- [ ] Keep `main.py` as composition layer only: startup, middleware, models, endpoints.
- [ ] Re-run the targeted tests until green.

### Task 3: Align local production stack with the lab architecture

**Files:**
- Modify: `06-lab-complete/docker-compose.yml`
- Create: `06-lab-complete/nginx.conf`
- Modify: `06-lab-complete/Dockerfile`
- Modify: `06-lab-complete/.env.example`

- [ ] Put Nginx in front of the app and keep Redis as the shared store.
- [ ] Ensure the app binds to the platform `PORT` in cloud and remains usable behind Nginx locally.
- [ ] Re-run the production checker and container-level smoke tests.

### Task 4: Finish delivery docs for deployment and CI/CD handoff

**Files:**
- Modify: `06-lab-complete/README.md`
- Modify: `06-lab-complete/railway.toml`
- Modify: `06-lab-complete/render.yaml`

- [ ] Document the local verification flow and Railway-first deployment path.
- [ ] Keep the next-step guidance narrowly focused on CI/CD.
- [ ] Verify the final file set and commands match the implemented stack.
