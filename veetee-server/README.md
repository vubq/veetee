# VeeTee Realtime Voice Assistant Backend Server (Local Non-Docker)

VeeTee Server phục vụ ESP32/Xiaozhi firmware nguyên bản và Web Client qua WebSocket. Hướng phát triển hiện tại là **tối ưu toàn bộ ở phía server, không yêu cầu sửa hoặc flash firmware tùy biến**. Plan triển khai được theo dõi tại [task plan](../task-plans/2026-09-08-server-khong-sua-fw.md).

- **LLM:** Omniroute Groq Qwen 3.6 27B streaming.
- **ASR mặc định:** NVIDIA Parakeet CTC 0.6B Vietnamese + Silero VAD local; Deepgram là provider dự phòng.
- **TTS:** VieNeu-TTS v3 Turbo local.
- **VAD:** end silence 450 ms; với frame Silero 32 ms, ngưỡng thực tế được vượt khoảng 480 ms.
- **Pipeline:** LLM sinh clause vào bounded queue `maxsize=3`; TTS xử lý tuần tự từng clause.
- **Barge-in mặc định:** `barge_in_policy=client_only`. ESP32 ngắt lượt bằng các message chuẩn đã có như `abort` hoặc `listen:start`.
- **Audio pacing:** server giới hạn lượng TTS gửi trước bằng `tts.send_ahead_ms`, mặc định 120 ms, thay cho fixed sleep 5 ms.
- **TTS backpressure:** queue stream VieNeu có giới hạn, mặc định `tts.stream_queue_max_chunks=4`.
- **Môi trường:** chạy trực tiếp trên máy, không Docker.

## Cấu trúc chính

```text
veetee-server/
├── config/
│   └── settings.py
├── core/
│   ├── audio_pacing.py
│   ├── audio_utils.py
│   ├── protocol.py
│   ├── session.py
│   └── providers/
├── docs/
│   ├── API_PROTOCOL.md
│   ├── ESP32_CONFIG.md
│   ├── PLAN.md
│   ├── SETUP.md
│   └── VOICE_PIPELINE_STATUS.md
├── patches/
│   └── xiaozhi-esp32-barge-in.patch  # Artifact thử nghiệm cũ, không cần áp
├── tests/
├── test_e2e.py
├── server.py
└── start.sh
```

## Khởi động

```bash
cd /home/quangvu/Project/veetee/veetee-server
./start.sh
```

- WebSocket cho ESP32/client: `ws://<IP_MAY_TINH>:8000/`
- OTA config endpoint: `http://<IP_MAY_TINH>:8003/ota/`
- Web dashboard: `http://<IP_MAY_TINH>:8003/`

## Kiểm thử

Regression/unit:

```bash
cd /home/quangvu/Project/veetee/veetee-server
/home/quangvu/Project/venv/bin/python -m unittest discover -s tests -v
```

Runtime E2E:

```bash
cd /home/quangvu/Project/veetee/veetee-server
PYTHONPATH=. /home/quangvu/Project/venv/bin/python test_e2e.py
```

`test_e2e.py` trả exit code lỗi khi thiếu marker/thứ tự sai và trả `BLOCKED` khi runtime/model cần thiết chưa sẵn sàng; không coi timeout là PASS.

## Tương thích firmware nguyên bản

Server không yêu cầu `features.device_aec`, không gửi `interrupt=true` trong đường mặc định và không yêu cầu `ResetDecoder()` custom. Hello stock có thể không có `features`, có `mcp`, hoặc có `aec` theo cấu hình firmware.

`features.aec=true` chỉ cho biết firmware chọn hướng server-side AEC; VeeTee hiện chưa có tầng server-side AEC nên cờ này **không** tự bật automatic speech barge-in. `mode=realtime` cũng không đủ để chứng minh echo cancellation đang hoạt động.

Khi client gửi `abort` hoặc `listen:start` lúc server đang nói, server hủy turn hiện tại, ngừng gửi audio mới và gửi `tts:stop` chuẩn. Firmware stock không có playback flush ACK chung, nên server chỉ có thể giảm đuôi âm bằng pacing; thời điểm loa thật sự dừng phải đo trên board.

## Tài liệu

- [Kiến trúc & kế hoạch voice pipeline](docs/PLAN.md)
- [Trạng thái triển khai](docs/VOICE_PIPELINE_STATUS.md)
- [Hướng dẫn ESP32 firmware nguyên bản](docs/ESP32_CONFIG.md)
- [Đặc tả protocol](docs/API_PROTOCOL.md)
- [Cài đặt server](docs/SETUP.md)
