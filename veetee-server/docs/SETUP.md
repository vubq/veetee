# Cài đặt và vận hành VeeTee Server

Cập nhật: **2026-09-09**

VeeTee chạy trực tiếp trên host Linux/WSL, không cần Docker. ASR/TTS mặc định chạy local; LLM đi qua OmniRoute và route phía sau có thể dùng provider từ xa tùy cấu hình.

## 1. Prerequisites

Cần Python 3 có `venv`, Git và các system library phục vụ audio/Opus. Ví dụ trên Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libopus-dev libopus0 git python3-venv python3-pip
```

Dependencies được pin trong [requirements.txt](../requirements.txt) (snapshot venv 2026-09-09, Python 3.12). `start.sh` cài từ file này khi virtualenv chưa tồn tại:

```bash
../../venv/bin/pip install -r requirements.txt
```

GPU/CUDA compatibility phụ thuộc environment thực tế. Không upgrade driver/Python/package chỉ để làm tài liệu khớp nếu server đang chạy ổn.

## 2. Tạo cấu hình local

Từ thư mục `veetee-server/`:

```bash
cp config.example.yaml config.yaml
```

`config.yaml` được Git bỏ qua. Dùng [config.example.yaml](../config.example.yaml) làm full reference thay vì copy một bản YAML dài vào tài liệu này.

Một số default đáng chú ý trong source/example hiện tại:

| Field | Default |
| --- | --- |
| `asr.min_silence_duration_ms` | `450` |
| `latency.first_token_timeout_ms` | `6000` |
| `latency.total_turn_timeout_ms` | `15000` |
| `tts.sample_rate` | `24000` |
| `tools.max_calls_per_turn` | `3` (cho phép `1..8`) |
| `tools.schema_limit` | `16` (max `64`) |
| `tools.max_llm_rounds_per_turn` | `2` (cho phép `1..4`) |
| `llm.base_prompt_max_bytes` / `base_prompt_max_tokens` | `32768` / `8000` est. |
| `tts.first_chunk_timeout_ms` / `stall_timeout_ms` | `4000` / `2500` |

Local `config.yaml` có thể override các giá trị này; local override không phải default của project.

## 3. Provider và tính local/remote

Default config dùng Parakeet CTC Vietnamese + Silero VAD cho ASR và VieNeu cho TTS. Deepgram là provider ASR thay thế khi operator cấu hình provider/API key phù hợp.

`asr.device` mặc định `cuda` nhưng server tự fallback `cpu` khi không có GPU (`core/providers/asr/parakeet_silero.py`) — vẫn chạy nhưng inference chậm hơn, không phù hợp đo latency SLA.

Secret ASR cấp qua env (để `asr.api_key` trống trong `config.yaml` local):

```bash
export DEEPGRAM_API_KEY='<secret>'
```

`settings.py` fallback về env khi YAML để trống, nên không ghi key thật vào file.

LLM provider `omniroute` gọi gateway được cấu hình trong `llm.base_url`. Nếu gateway route sang Groq hoặc provider khác thì inference LLM là remote dù process VeeTee/ASR/TTS vẫn chạy local.

## 4. Persona

Thứ tự hiện hành:

1. saved persona runtime nếu file state tồn tại;
2. nếu không có saved persona, dùng `llm.base_prompt` từ config;
3. `agent-base-prompt.txt` là prompt template chứa `{{base_prompt}}`.

Dashboard/API quản trị có thể cập nhật persona và persist cho lượt sau. API/UI/config/runtime dùng chung budget bytes + est. tokens (`llm.base_prompt_max_bytes`/`base_prompt_max_tokens`); over-budget bị reject rõ ràng, không truncate âm thầm. Đổi persona áp dụng từ lượt sau, các round trong cùng lượt giữ persona version nhất quán. Xem [ARCHITECTURE.md](ARCHITECTURE.md).

Không sao chép nội dung saved persona vào tài liệu hoặc benchmark artifact chỉ để debug config.

## 5. Management token

`/api/prompt` và `/api/test-voice` yêu cầu management token. Cách ưu tiên để cấp secret:

```bash
export VEETEE_MANAGEMENT_TOKEN='<secret>'
```

Có thể dùng `management.token` trong local config, nhưng không commit secret. Client management gửi một trong hai dạng:

```text
X-Veetee-Management-Token: <secret>
Authorization: Bearer <secret>
```

Khi token trống, management endpoints trả `401`. OTA và standalone WebSocket stock không dùng management credential này. Chi tiết nằm trong [API_PROTOCOL.md](API_PROTOCOL.md).

## 6. Khởi động

### Foreground bằng script

```bash
./start.sh
```

`start.sh` dùng virtualenv ở `../../venv` tương đối với thư mục server và chạy `server.py` với `PYTHONPATH` phù hợp.

### Chạy Python trực tiếp

Nếu virtualenv đã có dependency (luôn dùng venv của project, không dùng `python3` hệ thống):

```bash
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
../../venv/bin/python server.py
```

Chạy test cũng phải dùng venv này:

```bash
../../venv/bin/python -m unittest discover -s tests
```

`python3` hệ thống thiếu deps (đã ghi nhận 12 import errors) nên không dùng để kết luận test hỏng.

Endpoint mặc định:

```text
WebSocket: ws://<server>:8000/
OTA:       http://<server>:8003/ota/
Dashboard: http://<server>:8003/
```

## 7. Service mode

Chỉ chọn **một** process owner cho cùng port/GPU.

Nếu môi trường đã có user service:

```bash
systemctl --user status veetee-server-bg.service
systemctl --user restart veetee-server-bg.service
```

Nếu tự tạo systemd service mới, dùng placeholder theo máy của bạn thay vì copy user/path cá nhân:

```ini
[Unit]
Description=VeeTee Realtime Voice Server
After=network.target

[Service]
Type=simple
User=<service-user>
WorkingDirectory=<repo>/veetee-server
Environment=PYTHONPATH=<repo>/veetee-server
Environment=VEETEE_MANAGEMENT_TOKEN=<set-via-secure-environment>
ExecStart=<venv>/bin/python <repo>/veetee-server/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Không chạy thêm foreground instance nếu service hiện tại đã giữ `:8000`/`:8003` hoặc đang sử dụng cùng GPU model.

## 8. Kiểm tra và troubleshooting

Các lệnh test, benchmark và cách phân biệt unit/runtime/hardware nằm trong [TESTING.md](TESTING.md).

Nếu client không kết nối được:

1. xác nhận process chỉ chạy một instance;
2. kiểm port `8000` và `8003` đang listen;
3. kiểm OTA trả đúng WebSocket URL mà ESP32 truy cập được;
4. kiểm firewall/reverse proxy/Tailscale riêng với server process;
5. kiểm log provider ASR/LLM/TTS nếu WebSocket đã kết nối nhưng voice pipeline fail.

Không suy hardware AEC/playback từ log server gửi `tts:stop` hoặc last binary.
