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

Sao chép `config.example.yaml` thành `config.yaml`, sau đó điền API key cục bộ. `config.yaml` được Git bỏ qua để tránh commit credential.

```yaml
server:
  host: "0.0.0.0"
  ws_port: 8000      # Cổng WebSocket cho thiết bị ESP32
  http_port: 8003    # Cổng HTTP cho OTA và Web UI
  log_level: "INFO"

asr:
  provider: "deepgram"
  api_key: "YOUR_DEEPGRAM_API_KEY"
  model: "nova-2"
  language: "vi"
  smart_format: true
  interim_results: true
  endpointing_ms: 250
  sample_rate: 16000

llm:
  provider: "omniroute"
  base_url: "http://127.0.0.1:20128/v1"
  api_key: "local-omniroute"
  model: "groq/qwen/qwen3.6-27b"
  temperature: 0.6
  max_tokens: 350
  reasoning_format: "hidden"
  system_prompt: "Bạn là trợ lý ảo giọng nói tiếng Việt thông minh. Luôn trả lời trực tiếp trong 1-2 câu ngắn gọn, không giải thích dài dòng, không dùng markdown. Bắt đầu câu trả lời bằng một cảm xúc như [happy] hoặc [neutral]."

tts:
  provider: "vieneu"
  voice: "Xuân Vĩnh"  # Các giọng: Xuân Vĩnh, Trúc Ly, Minh Đức, Thái Sơn, Thùy Dung, Đoan Trang
  sample_rate: 24000
  frame_duration_ms: 60
  denoise: true
  temperature: 0.7
```

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
