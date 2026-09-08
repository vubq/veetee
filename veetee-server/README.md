# VeeTee Realtime Voice Assistant Backend Server (Local Non-Docker)

Server backend trợ lý ảo giọng nói thời gian thực cho phần cứng ESP32 và Web Client, được xây dựng theo chuẩn kiến trúc AI hiện đại:
- **LLM:** Omniroute Groq Qwen 3.6 27B (Streaming SSE, `reasoning_format: hidden`).
- **ASR mặc định:** NVIDIA Parakeet CTC 0.6B Vietnamese + Silero VAD local. Deepgram vẫn được giữ làm provider dự phòng.
- **TTS:** Vieneu-TTS v3 Turbo Neural TTS chạy trực tiếp trên GPU/CPU cục bộ.
- **Pipeline:** LLM sinh clause vào bounded queue để chạy chồng lấp với TTS; VAD end silence hiện là 450 ms.
- **Barge-in:** Realtime automatic barge-in chỉ bật khi firmware xác nhận `device_aec=true`; interruption dùng `tts:stop` + `interrupt=true` để flush audio buffer.
- **Môi trường:** Chạy trực tiếp trên máy trần (100% Non-Docker).

---

## 🚀 Cấu Trúc Thư Mục

```
veetee/veetee-server/
├── config/
│   └── settings.py          # Dataclass loader cấu hình từ config.yaml & env
├── config.yaml              # File cấu hình trung tâm (ASR, LLM, TTS, Ports)
├── core/
│   ├── protocol.py          # Giao thức truyền thông: Parser/Packer gói tin v1/v2/v3
│   ├── audio_utils.py       # Bộ mã hóa/giải mã Opus, Resampler Soxr, Frame chunker
│   ├── dialogue.py          # Quản lý bộ nhớ ngữ cảnh hội thoại nhiều lượt
│   ├── providers/
│   │   ├── asr/
│   │   │   ├── base.py              # Interface BaseASR
│   │   │   ├── parakeet_silero.py   # Parakeet CTC VI + Silero VAD local
│   │   │   └── deepgram_stream.py   # Deepgram provider dự phòng
│   │   ├── llm/
│   │   │   ├── base.py              # Interface BaseLLM
│   │   │   └── omniroute_groq.py    # Groq / Omniroute Qwen 3.6 27B Streaming Provider
│   │   └── tts/
│   │       ├── base.py              # Interface BaseTTS
│   │       └── vieneu_local.py      # Vieneu Neural TTS Local GPU Provider
│   └── session.py           # Quản lý phiên kết nối thiết bị, State Machine & Barge-in
├── docs/
│   ├── PLAN.md              # Kế hoạch kiến trúc & Phân tích độ trễ < 1s
│   ├── SETUP.md             # Hướng dẫn cài đặt chi tiết trên Linux/Ubuntu
│   ├── ESP32_CONFIG.md      # Hướng dẫn cấu hình phần cứng ESP32
│   ├── API_PROTOCOL.md      # Đặc tả kỹ thuật chi tiết giao thức truyền thông
│   └── VOICE_PIPELINE_STATUS.md # Tiến độ, task hoàn thành và checklist ESP32
├── patches/
│   └── xiaozhi-esp32-barge-in.patch # Patch cho firmware Xiaozhi tham khảo
├── static/
│   └── index.html           # Web Dashboard & Trình giả lập Client
├── http_server.py           # Server HTTP cho OTA config (/ota/) & Web UI
├── server.py                # Server chính khởi động WebSocket (8000) & HTTP (8003)
├── start.sh                 # Script khởi động tự động 1-click
└── test_e2e.py              # Test script kiểm thử toàn trình độ trễ & luồng thoại
```

---

## ⚡ Khởi Động Nhanh (Quick Start)

### 1. Khởi động server
```bash
cd /home/quangvu/Project/veetee/veetee-server
./start.sh
```

### 2. Các địa chỉ dịch vụ
- **WebSocket URL (cho ESP32/Client):** `ws://<IP_MAY_TINH>:8000/`
- **OTA Endpoint (cho ESP32 Auto-Config):** `http://<IP_MAY_TINH>:8003/ota/`
- **Web Dashboard (Thử nghiệm trên trình duyệt):** `http://<IP_MAY_TINH>:8003/`

### 3. Kiểm thử toàn trình (End-to-End Test)
```bash
PYTHONPATH=. /home/quangvu/Project/venv/bin/python test_e2e.py
```

---

## 📚 Tài Liệu Kèm Theo
- [Kế hoạch & Thiết kế Hệ thống](docs/PLAN.md)
- [Trạng thái Voice Pipeline & Task](docs/VOICE_PIPELINE_STATUS.md)
- [Hướng dẫn Cài đặt & Systemd Service](docs/SETUP.md)
- [Hướng dẫn Kết nối ESP32](docs/ESP32_CONFIG.md)
- [Đặc tả Giao thức Truyền Thông](docs/API_PROTOCOL.md)
