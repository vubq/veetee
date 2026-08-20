# Veetee Server backend

M1.1 tạo nền tảng FastAPI tối thiểu. Chưa có device WebSocket, database, migration hoặc
AI provider.

## Local commands

```bash
uv sync --dev
uv run pytest
uv run ruff check src tests
uv run mypy
uv run uvicorn veetee_server.app:app --app-dir src --host 127.0.0.1 --port 8080
```

Endpoints hiện có:

- `GET /healthz`: process liveness.
- `GET /readyz`: application readiness sau lifespan startup.

Runtime config dùng biến `VEETEE_`. Không đặt secret vào `.env.example` hoặc log.
