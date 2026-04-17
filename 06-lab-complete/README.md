# Lab 12 — Part 6 Final Project

`06-lab-complete` là bản hoàn chỉnh cho Part 6, gom toàn bộ các ý chính của Day 12 vào một project có thể chạy local, test được, và sẵn sàng deploy.

## Có gì trong project này

- Public web chat UI tại `GET /`
- API FastAPI được bảo vệ tại `POST /ask`
- Route public cho browser tại `POST /web/ask`
- Xác thực `X-API-Key` cho route API protected
- Gemini provider support qua `GEMINI_API_KEY` ở backend
- Lưu lịch sử hội thoại trong Redis
- Rate limit theo user: `10 request/phút`
- Budget guard theo tháng: mặc định `$10/user/tháng`
- Cost optimization bằng cách giới hạn context gửi vào model
- Health check: `GET /health`
- Readiness check: `GET /ready`
- Metrics cho Prometheus: `GET /metrics`
- OpenTelemetry tracing, trả `X-Trace-Id` trong response
- Structured JSON logging
- Chạy local theo mô hình `nginx + agent + redis + prometheus`
- Ưu tiên deploy Railway, fallback Render

## Cấu trúc chính

```text
06-lab-complete/
├── app/
│   ├── chat_service.py
│   ├── main.py
│   ├── config.py
│   ├── auth.py
│   ├── rate_limiter.py
│   ├── cost_guard.py
│   ├── gemini_client.py
│   └── web_ui.py
├── tests/test_app.py
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
├── prometheus.yml
├── railway.toml
├── render.yaml
└── check_production_ready.py
```

## Cách chạy code local

### 1. Chuẩn bị env

```bash
cd 06-lab-complete
cp .env.example .env.local
```

Nếu chỉ muốn chạy local theo mặc định thì chưa cần sửa gì thêm trong `.env.local`.

### 2. Chạy full stack local

```bash
docker compose up --build --scale agent=3
```

Các service chính:

- `nginx`: cổng `8080`
- `prometheus`: cổng `9090`
- `redis`: internal only
- `agent`: scale ra 3 instance để mô phỏng stateless/load balancing

### 3. Dừng stack

```bash
docker compose down
```

Nếu muốn xóa luôn volume Redis local:

```bash
docker compose down -v
```

## Cách test nhanh sau khi chạy

### Health check

```bash
curl http://localhost:8080/health
```

### Mở public chat UI

```bash
curl http://localhost:8080/
```

### Readiness check

```bash
curl http://localhost:8080/ready
```

### Gọi public web route

```bash
curl -X POST http://localhost:8080/web/ask \
  -H "Content-Type: application/json" \
  -d '{"nickname":"alice","question":"What is deployment?"}'
```

### Gọi protected API có API key

```bash
curl -X POST http://localhost:8080/ask \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"student-1","question":"What is deployment?"}'
```

### Xem metrics Prometheus

```bash
curl http://localhost:8080/metrics
```

### Mở Prometheus UI

```text
http://localhost:9090
```

## Cách test kỹ

### 1. Chạy unit/integration test của app

Do máy hiện tại đang dùng Python `3.14`, cách ổn định nhất là chạy test trong container `python:3.11` đúng với target runtime của lab:

```bash
docker run --rm \
  -v "$(pwd)/..:/workspace" \
  -w /workspace/06-lab-complete \
  python:3.11-slim \
  sh -lc "pip install --no-cache-dir -r requirements.txt >/tmp/pip.log && PYTHONPATH=/workspace/06-lab-complete pytest tests/test_app.py -q"
```

Test hiện có kiểm tra:

- `/` trả về HTML chat UI
- `/web/ask` hoạt động không cần `X-API-Key`
- Thiếu API key thì bị `401`
- Redis lỗi thì `/ready` trả `503`
- History hội thoại được giữ qua nhiều request
- Rate limit vượt ngưỡng thì trả `429`
- Budget vượt ngưỡng thì trả `402`
- `/metrics` có dữ liệu Prometheus

### 2. Chạy checker của bài lab

```bash
python check_production_ready.py
```

Checker sẽ rà:

- file bắt buộc
- auth / rate limit / cost guard
- health / ready / metrics
- Docker multi-stage
- nginx / redis / prometheus trong compose
- logging / tracing / Redis-backed history

### 3. Smoke test full stack local

Sau khi `docker compose up --build --scale agent=3`, chạy:

```bash
curl http://localhost:8080/
curl http://localhost:8080/health
curl http://localhost:8080/ready
curl http://localhost:8080/metrics
```

Test public chat route:

```bash
curl -i -X POST http://localhost:8080/web/ask \
  -H "Content-Type: application/json" \
  -d '{"nickname":"alice","question":"What is deployment?"}'
```

Kỳ vọng:

- HTTP `200`
- không cần `X-API-Key`
- JSON có `user_id` = `alice`
- JSON có `history_length`

Test auth fail:

```bash
curl -X POST http://localhost:8080/ask \
  -H "Content-Type: application/json" \
  -d '{"user_id":"student-1","question":"hello"}'
```

Kỳ vọng: `401`

Test gọi thành công:

```bash
curl -i -X POST http://localhost:8080/ask \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"student-1","question":"What is deployment?"}'
```

Kỳ vọng:

- HTTP `200`
- có header `X-Trace-Id`
- JSON có `served_by`
- JSON có `history_length`

Test load balancing + stateless:

```bash
for q in "What is deployment?" "Why do we need Docker?" "Explain health checks"; do
  curl -s -X POST http://localhost:8080/ask \
    -H "X-API-Key: dev-key-change-me" \
    -H "Content-Type: application/json" \
    -d "{\"user_id\":\"student-1\",\"question\":\"$q\"}"
  echo
done
```

Quan sát:

- `served_by` có thể đổi giữa các request
- `history_length` vẫn tăng dần

Test rate limit:

```bash
for i in $(seq 1 11); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:8080/ask \
    -H "X-API-Key: dev-key-change-me" \
    -H "Content-Type: application/json" \
    -d "{\"user_id\":\"ratelimit-user\",\"question\":\"req-$i\"}"
done
```

Kỳ vọng: các request đầu `200`, request vượt ngưỡng trả `429`.

Test budget guard nhanh:

1. Sửa `.env.local`:

```env
MONTHLY_BUDGET_USD=0.000002
```

2. Chạy lại stack:

```bash
docker compose down
docker compose up --build --scale agent=3
```

3. Gọi 2 lần:

```bash
curl -X POST http://localhost:8080/ask \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"budget-user","question":"one two three four five"}'

curl -X POST http://localhost:8080/ask \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"budget-user","question":"one two three four five"}'
```

Kỳ vọng: request đầu `200`, request sau trả `402`.

## Observability

### Prometheus

- scrape từ `GET /metrics`
- config nằm ở [prometheus.yml](/mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment/06-lab-complete/prometheus.yml)
- UI local: `http://localhost:9090`

### OpenTelemetry

- mỗi request được tạo span
- response trả `X-Trace-Id`
- có thể đẩy trace ra OTLP backend bằng env:

```env
OTEL_EXPORTER_OTLP_ENDPOINT=http://your-otel-collector:4318/v1/traces
```

## Cost optimization

Project đã có vài điểm tối ưu chi phí sẵn:

- `MODEL_CONTEXT_MESSAGES=6`: chỉ gửi một phần history gần nhất vào model
- `CONVERSATION_TTL_SECONDS=3600`: session cũ tự hết hạn trong Redis
- `UVICORN_WORKERS=1`: giảm idle resource trên instance nhỏ
- `RATE_LIMIT_PER_MINUTE=10`: tránh burst traffic gây tốn chi phí

## Deploy Railway

Railway là hướng ưu tiên.

### Cách deploy thử nghiệm trên Railway

1. Push repo này lên GitHub.
2. Trong Railway, tạo project mới.
3. Chọn `New` -> `GitHub Repo`, kết nối repo hiện tại.
4. Ở service web vừa tạo, vào `Settings` -> `Source` và đặt `Root Directory` = `06-lab-complete`.
5. Railway sẽ đọc [railway.toml](/mnt/shared/AI-Thuc-Chien/day12_ha-tang-cloud_va_deployment/06-lab-complete/railway.toml), build bằng `Dockerfile`, và health check qua `GET /ready`.
6. Thêm Redis service trong cùng project.
7. Vào tab `Variables` của web service và set tối thiểu:
   - `ENVIRONMENT=production`
   - `AGENT_API_KEY=<your-secret-key>`
   - `REDIS_URL=<Redis internal URL do Railway cấp>`
   - `LLM_PROVIDER=gemini`
   - `GEMINI_API_KEY=<your-gemini-key>`
   - `LLM_MODEL=gemini-3.1-flash-lite-preview`
   - `RATE_LIMIT_PER_MINUTE=10`
   - `MONTHLY_BUDGET_USD=10.0`
   - `LOG_LEVEL=INFO`
8. Redeploy service web sau khi set đủ biến môi trường.

### Cách test sau khi Railway deploy xong

Lấy domain Railway, ví dụ:

```text
https://your-app.up.railway.app
```

Test readiness:

```bash
curl -i https://your-app.up.railway.app/ready
```

Kỳ vọng: HTTP `200`.

Test metrics:

```bash
curl -i https://your-app.up.railway.app/metrics
```

Kỳ vọng: HTTP `200`, `content-type` là text metrics của Prometheus.

Test public UI route:

```bash
curl -i https://your-app.up.railway.app/
```

Kỳ vọng: HTTP `200`, `content-type` là `text/html`.

Test request thành công:

```bash
curl -i -X POST https://your-app.up.railway.app/ask \
  -H "X-API-Key: <your-secret-key>" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"railway-test-user","question":"What is deployment?"}'
```

Kỳ vọng:

- HTTP `200`
- có header `X-Trace-Id`
- JSON có `served_by`
- JSON có `history_length`

Test auth fail:

```bash
curl -i -X POST https://your-app.up.railway.app/ask \
  -H "Content-Type: application/json" \
  -d '{"user_id":"railway-test-user","question":"hello"}'
```

Kỳ vọng: HTTP `401`

Nếu `GET /ready` trả `503`, kiểm tra lại:

- `REDIS_URL` đã set đúng chưa
- Redis service có đang healthy không
- web service đã được redeploy sau khi thêm biến môi trường chưa

## Deploy Render

Dùng khi Railway build/runtime fail liên tục.

1. Push repo lên GitHub
2. Tạo service từ `render.yaml`
3. Provision Redis
4. Set các env:

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

5. Test lại `/`, `/ready`, `/metrics`, `/web/ask`, và `POST /ask`

## Next step

Phần tiếp theo chỉ còn CI/CD. Runtime stack, test flow, README và deploy config đã được chuẩn bị để gắn GitHub Actions ở bước sau.
