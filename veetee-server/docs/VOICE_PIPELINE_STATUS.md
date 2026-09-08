# Voice Pipeline Status

Cập nhật: **2026-09-08**

## Trạng thái tổng thể

Hướng triển khai hiện tại là **server-only, firmware nguyên bản**. Phần code server cho contract stock FW, cancellation, pacing, bounded queue/backpressure và stale-ASR protection đã hoàn thành ở mức regression. Hardware ESP32 stock FW và runtime model thật chưa được chứng nhận nên trạng thái tổng vẫn là **PARTIAL / hardware PENDING**.

## Đã hoàn thành

| Mức | Hạng mục | Trạng thái | Ghi chú |
| :--- | :--- | :---: | :--- |
| P1 | VAD end silence 650 -> 450 ms | ✅ | Threshold frame thực tế khoảng 480 ms |
| P1 | Stock FW protocol contract | ✅ | Hello thiếu features / `mcp` / `aec`, V1/V2/V3 |
| P1 | Barge-in policy mặc định | ✅ | `client_only`, không yêu cầu `device_aec` |
| P1 | Client abort/listen-start cancellation | ✅ | Dùng `tts:stop` chuẩn |
| P1 | Queue cancellation deadlock | ✅ | Không block producer khi consumer bị hủy |
| P1 | Turn/capture generation | ✅ | Chặn turn/audio/transcript cũ |
| P1 | Echo guard qua playback tail estimate | ✅ | Giảm self-turn khi chưa có AEC xác minh |
| P1 | Audio pacing | ✅ | Monotonic pacer, send-ahead mặc định 120 ms |
| P1 | TTS bounded backpressure | ✅ | VieNeu queue mặc định 4 chunk |
| P2 | LLM clause pipelining | ✅ | Queue `maxsize=3`, TTS vẫn tuần tự |
| P2 | E2E contract fail đúng | ✅ | Missing marker/order sai fail; runtime thiếu -> BLOCKED |
| P2 | Docs server-only | ✅ | Không yêu cầu patch/build FW tùy biến |

## Regression hiện tại

Suite hiện bao phủ protocol stock, audio pacing, lifecycle/cancel, stale ASR, TTS backpressure và E2E contract.

Kết quả gần nhất trước khi đồng bộ tài liệu:

```text
26/26 tests PASS
py_compile PASS
git diff --check PASS
```

Runtime E2E với VieNeu/Parakeet/API thật chưa được dùng để chứng minh build mới vì process server đang chạy được khởi động trước các thay đổi gần nhất. Không coi process cũ là bằng chứng cho code mới.

## Quyết định kỹ thuật

### ASR/VAD

- Primary: Parakeet CTC 0.6B Vietnamese + Silero VAD local.
- End silence: 450 ms.
- Deepgram là provider dự phòng.
- Capture generation loại callback transcript cũ sau abort/listen-start/close.

### LLM/TTS

- LLM producer -> `asyncio.Queue(maxsize=3)`.
- TTS consumer tổng hợp từng clause tuần tự.
- VieNeu thread -> async bridge có bounded queue và cancel-aware shutdown.
- Không chạy concurrent inference trên engine TTS dùng chung chỉ để giảm pause.

### Barge-in stock FW

- Mặc định `server.barge_in_policy=client_only`.
- Explicit stock `abort` và `listen:start` có thể cancel turn đang nói.
- Server gửi stop chuẩn, không phụ thuộc `interrupt=true`.
- Không dựa vào `device_aec`; stock hello không cần field này.
- `features.aec=true` hoặc `mode=realtime` không tự bật speech-start barge-in.
- Server dùng send-ahead pacing để giảm audio đã gửi trước; không cam kết loa dừng tức thì vì stock protocol không có playback flush ACK.

## Còn phải hoàn thành

| Ưu tiên | Task | Trạng thái |
| :--- | :--- | :---: |
| P2 | Runtime E2E trên code mới với model/API thật | PENDING |
| P2 | Đo ASR correction bật/tắt | PENDING |
| P1 | Test ESP32 bằng firmware nguyên bản đang có | PENDING |
| P1 | Test nhiều lượt + normal tail | PENDING |
| P1 | Test nút/wake-word abort nếu FW hỗ trợ | PENDING |
| P1 | Đo request-abort -> last binary server | PENDING |
| P1 | Đo thao tác ngắt -> loa vật lý dừng | PENDING |
| P2 | 20 normal + 20 interrupt, báo lỗi/p50/p95 | PENDING |
| P3 | Optional speech barge-in cho device profile có AEC xác minh độc lập | backlog |

Không có task build/flash patched FW trong đường nghiệm thu hiện tại.

## Tiêu chí hardware baseline

Chỉ đánh hardware stock FW hoàn thành khi có bằng chứng từ board thật:

1. OTA/WS hello và audio hai chiều ổn định.
2. Hội thoại nhiều lượt không sai state.
3. Normal TTS không mất đuôi câu.
4. Ngắt bằng cơ chế stock của board tạo turn mới ổn định nếu board/FW hỗ trợ.
5. Có số đo physical speaker stop, không dùng thời điểm JSON stop làm thay thế.
6. Có kết quả lặp và p50/p95 trong điều kiện test được ghi lại.

## File liên quan

- `core/session.py`: state machine, generation ownership, cancel, echo guard, pacing integration.
- `core/audio_pacing.py`: giới hạn send-ahead theo monotonic clock.
- `core/protocol.py`: stock protocol + binary V1/V2/V3 validation.
- `core/providers/asr/`: capture generation/stale callback filtering.
- `core/providers/tts/vieneu_local.py`: bounded backpressure.
- `tests/`: regression server-only/stock-FW.
- `test_e2e.py`: runtime/contract E2E với exit status rõ.
- `patches/xiaozhi-esp32-barge-in.patch`: artifact thử nghiệm lịch sử, không cần áp.
