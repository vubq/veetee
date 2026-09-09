# Hướng Dẫn Cài Đặt & Vận Hành (Local Setup Guide)

Dự án này được thiết kế để chạy **100% Local trên máy trần (Không dùng Docker)**, tận dụng trực tiếp GPU NVIDIA và Python 3.12+.

---

## 1. Yêu Cầu Hệ Thống (Prerequisites)

- **Hệ điều hành:** Linux (Ubuntu 22.04 / 24.04, Debian) hoặc WSL2.
- **Python:** Python 3.10, 3.11 hoặc 3.12 (`python3-venv`, `python3-pip`).
- **Thư viện hệ thống:**
  ```bash
  sudo apt-get update
  sudo apt-get install -y ffmpeg libopus-dev libopus0 git
  ```
- **Phần cứng đề xuất:**
  - CPU: 4 cores trở lên.
  - GPU: NVIDIA GPU (GTX 1650 Ti / RTX series, CUDA 12.x) hoặc CPU đa luồng.
  - RAM: 8GB trở lên.

---

## 2. Cài Đặt Môi Trường Ảo (Virtualenv Setup)

Từ thư mục gốc `veetee-server`:

```bash
# Di chuyển vào thư mục server
cd /home/quangvu/Project/veetee/veetee-server

# Tạo môi trường ảo (nếu chưa có)
python3 -m venv ../../venv

# Kích hoạt môi trường ảo
source ../../venv/bin/activate

# Cài đặt các gói phụ thuộc
pip install --upgrade pip
pip install vieneu websockets aiohttp numpy soundfile scipy opuslib-next pyyaml soxr
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install transformers accelerate
```

---

## 3. Cấu Hình Hệ Thống (`config.yaml`)

Sao chép `config.example.yaml` thành `config.yaml`, sau đó chỉnh theo máy chạy server. `config.yaml` được Git bỏ qua để tránh commit credential. Mặc định hiện tại dùng Parakeet Vietnamese + Silero VAD local; Deepgram chỉ là provider thay thế.

```yaml
server:
  host: "0.0.0.0"
  ws_port: 8000      # Cổng WebSocket cho thiết bị ESP32
  http_port: 8003    # Cổng HTTP cho OTA và Web UI
  log_level: "INFO"
  barge_in_policy: "client_only"

asr:
  provider: "parakeet_silero"
  api_key: ""
  model: "nvidia/parakeet-ctc-0.6b-vi"
  language: "vi"
  smart_format: true
  interim_results: true
  endpointing_ms: 250
  sample_rate: 16000
  device: "cuda"
  vad_model_path: "models/silero-vad/silero_vad.onnx"
  vad_threshold: 0.5
  vad_threshold_low: 0.3
  min_silence_duration_ms: 450
  min_speech_duration_ms: 160
  speech_start_frames: 2
  pre_speech_pad_ms: 512
  text_correction_enabled: false
  text_correction_confidence_threshold: 0.78
  text_correction_timeout_ms: 900

llm:
  provider: "omniroute"
  base_url: "http://127.0.0.1:20128/v1"
  api_key: "local-omniroute"
  model: "groq/qwen/qwen3.6-27b"
  temperature: 0.6
  max_tokens: 600
  reasoning_format: "hidden"
  base_prompt: "Bạn là VeeTee, một trợ lý ảo giọng nói tiếng Việt thông minh, thân thiện và hữu ích."
  prompt_template: "agent-base-prompt.txt"

tts:
  provider: "vieneu"
  voice: "Xuân Vĩnh"  # Các giọng: Xuân Vĩnh, Trúc Ly, Minh Đức, Thái Sơn, Thùy Dung, Đoan Trang
  source_voice: "Xuân Vĩnh"
  sample_rate: 24000
  frame_duration_ms: 60
  send_ahead_ms: 120
  stream_queue_max_chunks: 4
  denoise: true
  temperature: 0.7

# Hội thoại tự nhiên phía server. Semantic routing luôn do AI quyết định.
conversation:
  enabled: false
  # Legacy/inert compatibility data: vẫn đọc được config cũ nhưng không match user text.
  wake_words:
    - "你好小智"
    - "小爱同学"
    - "小美同学"
    - "VeeTee ơi"
  greeting_enabled: true
  greeting_ai_enabled: true
  greeting_pool_size: 3
  greeting_text: ""
  audio_cache_enabled: true
  idle_timeout_seconds: 120  # 0 = tắt idle timeout
  exit_commands:
    - "tạm biệt"
    - "kết thúc trò chuyện"
    - "thoát trò chuyện"
  goodbye_enabled: true
  goodbye_ai_enabled: true
  end_intent_ai_enabled: true
  ai_control_timeout_ms: 1800
  goodbye_text: ""
  wake_start_wait_ms: 150
  fixed_response_timeout_seconds: 5
  close_grace_ms: 250

latency:
  unified_turn_enabled: true
  first_token_timeout_ms: 4000
  total_turn_timeout_ms: 15000
  context_lookup_timeout_ms: 10

intent:
  enabled: true
  semantic_end_enabled: true
  confirmation_ttl_seconds: 15

memory:
  enabled: true
  durable_enabled: false
  database_path: "data/memory.sqlite3"
  trusted_owner_id: ""
  lookup_timeout_ms: 10
  top_k: 6
  max_memory_chars: 2400

tools:
  enabled: true
  native_enabled: true
  mcp_device_enabled: false
  max_calls_per_turn: 3
  schema_limit: 16
  max_llm_rounds_per_turn: 2
  tool_result_synthesis: true
```

Nếu chuyển `asr.provider` sang `deepgram`, đặt `model: "nova-2"` (hoặc model Deepgram phù hợp) và điền `api_key`; các key `smart_format`, `interim_results`, `endpointing_ms`, `language` và `sample_rate` sẽ được truyền vào kết nối Deepgram.

Khi `conversation.enabled=true`, `listen:detect` có text được đưa vào AI theo ngữ cảnh sau cửa sổ phối hợp `listen:start`; server không so text với `wake_words`/`exit_commands`. Các field `wake_words`, `exit_commands`, `greeting_text`, `goodbye_text`, `greeting_pool_size` chỉ còn để tương thích cấu hình cũ và không tự kích hoạt close/memory/confirmation/greeting. Không cần build/flash firmware mới.

`latency.unified_turn_enabled=true` yêu cầu `asr.text_correction_enabled=false`, vì fast path không cho phép một LLM correction phụ trước chat. Durable memory chỉ bật khi `memory.trusted_owner_id` được cấu hình phía operator; header `Device-Id`/`Client-Id` tự khai báo không đủ quyền làm owner. Chat thường dùng 1 LLM call; turn có action/receipt được phép thêm đúng 1 vòng synthesis, tổng tối đa 2 và vòng 2 không được gọi tool/action mới.

Idle timeout không tự suy ra người dùng muốn kết thúc. Mỗi inactivity epoch tạo tối đa một AI semantic evaluation; AI có thể `continue` để re-arm epoch hoặc `end` để kết thúc logical conversation. Recovery speech khi LLM/TTS live lỗi chỉ dùng asset đã được AI sinh trước và cache; không có literal fallback do server tự viết.

Trong môi trường development hiện tại, server được supervisor bằng user unit `veetee-server-bg.service`. Khi unit này đang chạy, dùng `systemctl --user` để kiểm tra/restart và không mở thêm foreground server tranh GPU/port.

---

## 4. Khởi Động Server

### Cách 1: Sử dụng script tự động
```bash
cd /home/quangvu/Project/veetee/veetee-server
./start.sh
```

### Cách 2: Chạy trực tiếp qua Python
```bash
cd /home/quangvu/Project/veetee/veetee-server
export PYTHONPATH="/home/quangvu/Project/veetee/veetee-server:$PYTHONPATH"
/home/quangvu/Project/venv/bin/python server.py
```

Khi server khởi động thành công, màn hình sẽ hiển thị:
```
============================================================
  🚀 VeeTee Realtime Server is READY (Local Non-Docker)
  • WebSocket URL: ws://<IP_LOCAL>:8000/
  • OTA URL:       http://<IP_LOCAL>:8003/ota/
  • Web Dashboard: http://<IP_LOCAL>:8003/
============================================================
```

---

## 5. Kiểm Thử Toàn Trình (End-to-End Verification)

Chạy script test toàn diện (mô phỏng người dùng nói câu hỏi tiếng Việt, nhận diện Deepgram, phản hồi LLM Qwen 3.6 và phát TTS Vieneu):

```bash
cd /home/quangvu/Project/veetee/veetee-server
PYTHONPATH=. /home/quangvu/Project/venv/bin/python test_e2e.py
```

---

## 6. Thiết Lập Tự Khởi Động Cùng Hệ Thống (Systemd Service)

Để server tự động chạy ngầm khi bật máy tính:

1. Tạo file service `/etc/systemd/system/veetee-server.service`:
```ini
[Unit]
Description=VeeTee Realtime Voice Server
After=network.target

[Service]
Type=simple
User=quangvu
WorkingDirectory=/home/quangvu/Project/veetee/veetee-server
Environment="PYTHONPATH=/home/quangvu/Project/veetee/veetee-server"
ExecStart=/home/quangvu/Project/venv/bin/python /home/quangvu/Project/veetee/veetee-server/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

2. Kích hoạt và bật service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable veetee-server
sudo systemctl start veetee-server
sudo systemctl status veetee-server
```
