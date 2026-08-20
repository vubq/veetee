# Báo cáo kiểm thử Mốc 1

## Phạm vi

Mốc 1 tạo backend FastAPI typed nhận session thiết bị qua OTA/config discovery và Device
WebSocket, quản lý state/turn/generation, validate audio v1/v2/v3, chạy fake AI pipeline
deterministic và cung cấp simulator Veetee độc lập đọc golden vectors.

## Bằng chứng tự động

Ngày chạy: 2026-08-21. Client: simulator Veetee M1.7; không dùng `digital-human` hoặc
thiết bị thật vì Mốc 1 chỉ yêu cầu fake provider/simulator.

| Gate | Kết quả |
| --- | --- |
| `uv run pytest` | 195 passed |
| `uv run ruff check src tests` | passed |
| `uv run mypy` | passed, strict |
| `uv lock --check && uv sync --check` | lock và environment đồng bộ |
| `python3 tools/scan_namespace.py` | passed, references excluded |
| Simulator contract suite lặp 3 lần | 13/13 mỗi lần, không flaky |
| Hai repo `references/` | tracked worktree sạch |

Coverage theo acceptance:

- OTA golden request/response, URL/token và no-update response.
- WebSocket auth/hello, strict JSON/binary validation, heartbeat, timeout, reconnect và
  graceful cleanup.
- Audio wire v1/v2/v3, malformed/truncated/oversized, bounded queue, pacing và generation.
- Luồng `hello -> listen -> audio -> stt -> tts -> audio -> stop` trên cả v1/v2/v3.
- Abort/barge-in, stale suppression, no-utterance, slow-client close `1009`.
- Bốn session mở đồng thời, session ID/output isolation, reconnect tạo session mới.
- Registry shutdown đóng active simulator bằng `1012`; OpenAPI/source product qua
  namespace policy.

## Demo local thật

Server được chạy trực tiếp tại `127.0.0.1:18080` với config test và simulator kết nối qua
HTTP/WebSocket thật. Demo OTA-discover đúng URL runtime, hello thành công, gửi golden
audio v1, nhận STT/TTS và 3 binary packet, nhận `tts/stop`, rồi goodbye sạch. OTA token
được redact khỏi output. Process test đã dừng sau demo.

## Giới hạn

- Fake codec/VAD/ASR/LLM/TTS chỉ chứng minh orchestration và contract, không chứng minh
  native Opus, chất lượng tiếng Việt, latency provider hoặc âm thanh nghe được.
- Chưa chạy `digital-human` hay board thật; các kiểm tra đó thuộc Mốc 2.
- TestClient hiện phát `StarletteDeprecationWarning` về `httpx`; đây là warning dependency,
  không ảnh hưởng kết quả. Chuyển test stack sẽ được thực hiện khi FastAPI/Starlette hỗ
  trợ đường nâng cấp ổn định, tránh thêm compatibility layer ngoài phạm vi M1.
