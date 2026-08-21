# Veetee Server backend

M1.1 tạo nền tảng FastAPI tối thiểu; M1.3 thêm Device WebSocket; M1.4 thêm OTA/config
discovery responder; M1.5 thêm audio primitives; M1.6 nối fake VAD/ASR/LLM/TTS
deterministic thành luồng device hoàn chỉnh; M2.1 thêm Silero VAD và M2.2 thêm PhoWhisper
ASR. Native Opus/resampler, database, LLM và TTS thật chưa được tích hợp.

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

M1.7 cung cấp simulator đọc golden vector tại `../contracts/device/`. Chạy server ở một
terminal, rồi chạy demo ở terminal khác:

```bash
VEETEE_DEVICE_GATEWAY_TOKEN='<local-token>' uv run veetee-simulator \
  --url http://127.0.0.1:8080 --protocol-version 1 --demo --send-goodbye
```

Demo gọi OTA discovery, lấy URL/token runtime, thực hiện
`hello -> listen -> audio -> stt -> tts/audio -> stop -> goodbye` và fail nếu thứ tự
contract hoặc downlink framing sai. Đổi `--protocol-version` thành `2` hoặc `3` để kiểm
tra wire format tương ứng. Token OTA được redact khỏi log; không truyền secret production
trên command line.

Runtime config dùng biến `VEETEE_` (xem `.env.example`). Không đặt secret vào
`.env.example` hoặc log.

Để bật Silero VAD M2.1, cung cấp model ONNX v5 đã kiểm checksum theo tài liệu pipeline:

```bash
VEETEE_VAD_PROVIDER=silero_onnx \
VEETEE_VAD_MODEL_PATH=/absolute/path/to/silero_vad.onnx \
uv run uvicorn veetee_server.app:app --host 127.0.0.1 --port 8080
```

Model không được tự tải khi startup; thiếu dependency/artifact hoặc warmup lỗi làm
`/readyz` trả `503` thay vì âm thầm rơi về fake VAD.

PhoWhisper M2.2 mặc định chỉ dùng model đã có trong local Hugging Face cache:

```bash
VEETEE_ASR_PROVIDER=pho_whisper \
VEETEE_ASR_MODEL_ID=mad1999/pho-whisper-small-ct2 \
VEETEE_ASR_LOCAL_FILES_ONLY=true \
uv run uvicorn veetee_server.app:app --host 127.0.0.1 --port 8080
```

Thiếu model local hoặc warmup lỗi làm `/readyz` trả `503`; server không tự tải model khi
startup theo cấu hình mặc định. Dependency Linux x86_64 bao gồm CUDA 12 cuBLAS/cuDNN
project-local để CTranslate2 không phụ thuộc vào venv khác. Không log PCM hay transcript
trong runtime provider.
