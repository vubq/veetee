# Veetee Server backend

M1.1 tạo nền tảng FastAPI tối thiểu; M1.3 thêm device WebSocket; M1.4 thêm OTA/config
discovery responder. Chưa có database, migration hoặc AI provider.

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
- `GET/POST/OPTIONS /api/v1/devices/ota/check`: OTA/config discovery cho firmware
  (server time, WebSocket URL/token, firmware no-update). Contract chi tiết ở
  `../docs/protocols-and-apis.md`; golden vector ở `../contracts/device/`.
- `WS /api/v1/devices/ws`: device gateway (hello/listen/abort/ping/pong/goodbye).

Runtime config dùng biến `VEETEE_` (xem `.env.example`). Không đặt secret vào
`.env.example` hoặc log.
