# VeeTee Realtime Voice Assistant Backend

VeeTee là backend voice realtime chạy trực tiếp trên Linux/WSL, phục vụ Web Client và ESP32/Xiaozhi **firmware nguyên bản**. Các tính năng chuẩn của server không yêu cầu patch/build/flash firmware tùy biến.

Pipeline mặc định dùng ASR local (Parakeet CTC Vietnamese + Silero VAD), LLM qua OmniRoute và TTS VieNeu local. Process server chạy local; route LLM phía sau OmniRoute có thể là provider từ xa tùy cấu hình.

## Khởi động nhanh

```bash
cd veetee-server
cp config.example.yaml config.yaml
./start.sh
```

`config.yaml` là cấu hình local và được Git bỏ qua. Dùng [config.example.yaml](config.example.yaml) làm reference đầy đủ; không sao chép credential vào repo.

Endpoint mặc định:

- WebSocket ESP32/client: `ws://<server>:8000/`
- OTA config: `http://<server>:8003/ota/`
- Web dashboard: `http://<server>:8003/`

## Nguyên tắc tương thích

- Semantic intent, chọn tool/function, confirmation, memory mutation, ngôn ngữ và kết thúc hội thoại do AI quyết định từ context/tool schema.
- Server giữ protocol/lifecycle, validation, permission/ownership, deadline/cancel, execution safety và receipt/state invariants.
- Stock `abort` và `listen:start` có thể ngắt lượt đang nói; server dùng `tts:stop` chuẩn.
- `features.aec=true` hoặc `mode=realtime` không được xem là bằng chứng AEC đang hoạt động đủ tốt cho automatic speech barge-in.
- Thời điểm server gửi xong audio không chứng minh loa ESP32 đã phát xong hoặc đã dừng vật lý.

## Tài liệu hiện hành

- [ARCHITECTURE.md](docs/ARCHITECTURE.md): kiến trúc hiện tại, ranh giới AI/server và các `KNOWN_GAP`.
- [SETUP.md](docs/SETUP.md): cài đặt, config, persona, management API và cách chạy service.
- [API_PROTOCOL.md](docs/API_PROTOCOL.md): contract OTA/WebSocket/MCP stock và các endpoint management riêng.
- [ESP32_CONFIG.md](docs/ESP32_CONFIG.md): kết nối firmware Xiaozhi nguyên bản và smoke test cơ bản.
- [TESTING.md](docs/TESTING.md): unit/integration/runtime/hardware, metric và quality gate.
- [VOICE_PIPELINE_STATUS.md](docs/VOICE_PIPELINE_STATUS.md): snapshot evidence/trạng thái hiện tại.
- [Task plans](../task-plans/README.md): kế hoạch runtime còn mở và lịch sử handoff.

`docs/PLAN.md` được giữ lại làm đường dẫn lịch sử và chỉ chuyển hướng tới các tài liệu hiện hành ở trên.

## Kiểm thử nhanh

```bash
../../venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=. ../../venv/bin/python test_e2e.py
../../venv/bin/python scripts/benchmark_pipeline.py --help
```

Luôn dùng venv của project (`../../venv`); `python3` hệ thống thiếu deps.

Chi tiết cách diễn giải kết quả và khi nào được ghi `PASS`, `PARTIAL` hoặc `PENDING` nằm trong [docs/TESTING.md](docs/TESTING.md).
