# Veetee Server backend

M1.1 tạo nền tảng FastAPI tối thiểu; M1.3 thêm Device WebSocket; M1.4 thêm OTA/config
discovery responder; M1.5 thêm audio primitives; M1.6 nối fake VAD/ASR/LLM/TTS
deterministic thành luồng device hoàn chỉnh; M2.1 thêm Silero VAD và M2.2 thêm PhoWhisper
ASR, M2.3 thêm OmniRoute Groq LLM, M2.5 thêm Gemini TTS và M2.7 thêm native Opus cùng
digital-human compatibility harness. M3 thêm prompt/dialogue, intent strategy, tool/MCP
và memory boundary; M4 thêm PostgreSQL control plane; M5 thêm activation/binding,
credential thiết bị và OTA artifact/release/rollout/report. Resampler khác sample rate
chưa được tích hợp.

## Local commands

```bash
uv sync --dev
uv run pytest
uv run ruff check src tests
uv run mypy
uv run uvicorn veetee_server.app:app --app-dir src --host 127.0.0.1 --port 8080
```

Native Opus dùng `libopus` của hệ điều hành và fail-closed ở readiness khi thư viện không
sẵn sàng:

```bash
VEETEE_AUDIO_CODEC=native uv run uvicorn veetee_server.app:app \
  --app-dir src --host 127.0.0.1 --port 8080 --no-access-log
```

Để chạy browser client đã pin qua adapter local, khởi động compatibility harness ở một
terminal khác. Harness chỉ phục vụ static asset read-only, proxy OTA và chuyển đổi wire
message; token gateway được đổi thành ticket một lần, TTL ngắn:

```bash
VEETEE_HARNESS_SERVER_HTTP_URL=http://127.0.0.1:8080 \
VEETEE_HARNESS_SERVER_WEBSOCKET_URL=ws://127.0.0.1:8080/api/v1/devices/ws \
uv run uvicorn veetee_server.digital_human_harness.app:app \
  --app-dir src --host 127.0.0.1 --port 8006 --no-access-log
```

Mở `http://127.0.0.1:8006/index.html`. Wake-word bridge của browser harness được báo rõ
`enabled=false`; harness không giả lập wake detection và không thay endpoint/auth product.

Endpoints hiện có:

- `GET /healthz`: process liveness.
- `GET /readyz`: application readiness sau lifespan startup.
- `GET/POST/OPTIONS /api/v1/devices/ota/check`: OTA/config discovery cho firmware
  (server time, WebSocket URL/token, firmware no-update). Contract chi tiết ở
  `../docs/protocols-and-apis.md`; golden vector ở `../contracts/device/`.
- `WS /api/v1/devices/ws`: device gateway (hello/listen/abort/ping/pong/goodbye).
- `POST /api/v1/devices/ota/report`: report OTA idempotent có device credential.
- `GET /api/v1/devices/ota/artifacts/{artifact_id}`: download streaming có token/range.
- `/api/v1/control/devices` và `/api/v1/control/ota`: lifecycle/OTA control plane.

Khi bật persistence ngoài local/test, phải cấu hình `VEETEE_OTA_PUBLIC_BASE_URL` bằng
HTTPS origin public tin cậy và `VEETEE_DEVICE_WEBSOCKET_PUBLIC_URL` bằng `wss://`.
`http://`/`ws://` chỉ được phép khi environment là `local` hoặc `test`; server không tạo
artifact URL từ request `Host`. OTA fleet API là admin-only, còn bind/recovery thiết bị
giữ kiểm tra owner/admin theo ownership.

Production activation yêu cầu admin provision trước `Device-Id`, `Client-Id` tùy chọn và
Ed25519 public key raw 32 byte. Discovery đầu chỉ trả nonce; thiết bị ký canonical
challenge rồi gửi `Activation-Nonce`/`Activation-Proof`. Chỉ response proof hợp lệ mới trả
code để thiết bị hiển thị vật lý. `VEETEE_ALLOW_INSECURE_ACTIVATION=true` chỉ được phép ở
`local`/`test`, mặc định tắt và bị readiness/config từ chối ở môi trường khác.

OTA report có quota persistent theo thiết bị/giờ, dedupe window và retention days cấu hình
được. Không có xóa tự động; operator chỉ gọi cleanup repository có chủ đích sau khi đã lưu
bằng chứng/audit cần thiết.

Module `veetee_server.audio` cung cấp parser/encoder binary frame, PCM format contract,
bounded ingress/egress queue và pacing. Golden vectors nằm trong `../contracts/device/`.
Các biến `VEETEE_AUDIO_*` trong `.env.example` giới hạn queue và pacing; chúng không chứa
credential.

M1.6 nhận audio hợp lệ trong khoảng `listen/start` đến `listen/stop`, rồi phát theo thứ
tự `stt`, `tts/start`, `tts/sentence_start`, binary audio đã đóng frame theo protocol và
`tts/stop`. Các biến `VEETEE_PIPELINE_*` chỉ cấu hình pipeline local; với provider mặc
định `fake`, pipeline không gọi model, mạng hoặc secret. `abort` và `listen/start` mới tăng I/O generation, purge
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

OmniRoute Groq M2.3 dùng OpenAI-compatible streaming API tại local gateway:

```bash
VEETEE_LLM_PROVIDER=omniroute \
VEETEE_LLM_API_KEY='<local-secret>' \
VEETEE_LLM_OMNIROUTE_MODEL=groq/openai/gpt-oss-120b \
uv run uvicorn veetee_server.app:app --host 127.0.0.1 --port 8080
```

Key chỉ đọc từ environment và không được log. Thiếu key làm `/readyz` trả `503` thay vì
rơi về fake LLM. Adapter không tự fallback sang Qwen; có thể đổi model bằng config sau khi
đã duyệt policy.

M2.4 chuyển text delta trực tiếp qua token-to-TTS segmenter và bắt đầu TTS khi đoạn đầu
sẵn sàng, không chờ LLM hoàn tất. Các ngưỡng mặc định là 24 ký tự cho đoạn đầu, 48 ký tự
cho đoạn sau, hard bound 220 ký tự và max wait 0,35 giây; xem `.env.example` để cấu hình.
Normalization bỏ Markdown, URL thô và ký hiệu không phù hợp để đọc thành tiếng.

Gemini native TTS M2.5 được bật bằng key pool local:

```bash
VEETEE_TTS_PROVIDER=gemini \
VEETEE_TTS_GEMINI_API_KEYS='<key-1>,<key-2>' \
VEETEE_TTS_GEMINI_MAIN_MODEL=gemini-3.1-flash-tts-preview \
uv run uvicorn veetee_server.app:app --host 127.0.0.1 --port 8080
```

Key chỉ đọc từ environment/secret local và không xuất hiện trong log hay response.
Adapter stream PCM 24 kHz mono s16le từ model 3.1 vào encoder theo frame 60 ms, không tạo
file tạm. Model 2.5 buffered chỉ được thử khi đặt
`VEETEE_TTS_ENABLE_FALLBACK_MODEL=true`; mặc định không fallback. Thiếu key hoặc runtime
không sẵn sàng làm startup/config hoặc `/readyz` fail-closed.
