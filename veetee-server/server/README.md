# Veetee Server backend

M1.1 tạo nền tảng FastAPI tối thiểu; M1.3 thêm Device WebSocket; M1.4 thêm OTA/config
discovery responder; M1.5 thêm audio primitives; M1.6 nối fake VAD/ASR/LLM/TTS
deterministic thành luồng device hoàn chỉnh. Native Opus/resampler, database và provider
AI thật chưa được tích hợp.

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

Module `veetee_server.audio` cung cấp parser/encoder binary frame, PCM format contract,
bounded ingress/egress queue và pacing. Golden vectors nằm trong `../contracts/device/`.
Các biến `VEETEE_AUDIO_*` trong `.env.example` giới hạn queue và pacing; chúng không chứa
credential.

M1.6 nhận audio hợp lệ trong khoảng `listen/start` đến `listen/stop`, rồi phát theo thứ
tự `stt`, `tts/start`, `tts/sentence_start`, binary audio đã đóng frame theo protocol và
`tts/stop`. Các biến `VEETEE_PIPELINE_*` chỉ cấu hình fake pipeline local; pipeline không
gọi model, mạng hoặc secret. `abort` và `listen/start` mới tăng I/O generation, purge
control/audio cũ và reset pacer.

Runtime config dùng biến `VEETEE_` (xem `.env.example`). Không đặt secret vào
`.env.example` hoặc log.
