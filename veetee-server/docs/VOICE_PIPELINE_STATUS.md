# Voice Pipeline Status

Cập nhật: **2026-09-08**

## Trạng thái tổng thể

Voice pipeline đã hoàn thành phần tối ưu server và lớp tương thích firmware cần thiết cho VAD endpointing, LLM/TTS pipelining và AEC-gated barge-in. Phần còn thiếu để chốt trạng thái production-ready là **build + test trên ESP32 thật với device-side AEC**.

## Hoàn thành

| Mức | Hạng mục | Trạng thái | Ghi chú |
| :--- | :--- | :---: | :--- |
| P1 | VAD end silence 650 -> 450 ms | ✅ | Silero frame 32 ms nên điểm chốt thực tế khoảng 480 ms |
| P1 | Barge-in khi robot đang nói | ✅ Server/FW logic | Tự động chỉ khi `realtime + device_aec=true` |
| P1 | Flush audio khi interruption | ✅ | `tts:stop` + `interrupt=true`; FW gọi `ResetDecoder()` |
| P1 | Fix queue cancellation deadlock | ✅ | Producer không block trong `finally` khi queue đầy |
| P1 | Abort state/bookkeeping cleanup | ✅ | Reset state, transcript, VAD/echo guard liên quan |
| P1 | Echo guard khi AEC chưa xác nhận | ✅ | Tránh loa tự tạo user turn |
| P2 | LLM sinh trước khi TTS xử lý câu hiện tại | ✅ | Bounded queue `maxsize=3` |
| P2 | Phân biệt normal stop / interrupt stop | ✅ | Normal stop drain tail; interrupt stop flush ngay |
| P2 | Firmware quảng bá AEC capability | ✅ Patch | `device_aec` và `aec` có semantics riêng |

## Đã kiểm tra bằng mô phỏng/local

```text
queue_cancel=ok
queue_normal_full=ok
abort_state=ok
aec_gate=ok
echo_tail_guard=ok
tts_stop_semantics=ok
```

Ngoài ra:

- Python compile check: ✅
- `git diff --check`: ✅
- Full ESP-IDF build: ⏳ chưa chạy vì máy hiện tại không có `idf.py`
- Physical ESP32/AEC test: ⏳ chưa chạy

## Quyết định kỹ thuật hiện tại

### ASR/VAD

- Primary: **Parakeet CTC 0.6B Vietnamese + Silero VAD local**.
- VAD silence threshold: **450 ms**.
- Deepgram vẫn được giữ làm provider dự phòng.

### LLM/TTS

- LLM stream được producer đưa clause vào `asyncio.Queue(maxsize=3)`.
- TTS consumer lấy clause và tổng hợp tuần tự.
- Thiết kế này cho LLM chạy trước trong lúc TTS bận nhưng không chạy đồng thời nhiều TTS inference.

### Barge-in

- `mode=realtime + device_aec=true`: cho phép speech-start tự ngắt TTS.
- Không xác nhận device AEC: giữ echo guard.
- `features.aec=true`: chỉ biểu thị server-side AEC; hiện chưa được VeeTee triển khai.
- `abort` và explicit `listen:start` trong lúc speaking vẫn có thể interrupt ngay bằng protocol command.

## Còn phải hoàn thành

| Ưu tiên | Task | Trạng thái |
| :--- | :--- | :---: |
| P1 | Build firmware Xiaozhi đã áp patch | ⏳ |
| P1 | Xác nhận device-side AEC thực sự hoạt động trên board | ⏳ |
| P1 | Test barge-in khi loa đang phát | ⏳ |
| P1 | Đo self-trigger/false-trigger do echo | ⏳ |
| P1 | Xác nhận decoder buffer được flush ngay khi `interrupt=true` | ⏳ |
| P2 | Đo speech-end -> first-audio latency trên ESP32 thật | ⏳ |
| P2 | Tune Silero threshold theo mic/loa thực tế nếu cần | ⏳ |
| P3 | Cân nhắc server-side AEC nếu cần hỗ trợ board không có device AEC | backlog |

## Tiêu chí để đánh dấu barge-in là hoàn thành trên ESP32

Chỉ đổi physical barge-in sang ✅ khi đạt cả bốn điều kiện:

1. Không self-trigger trong bài test loa phát bình thường.
2. Người dùng nói chen làm loa dừng tức thì và không phát lại audio cũ.
3. Câu nói chen được nhận dạng và xử lý thành turn mới ổn định.
4. Chạy lặp nhiều lượt không treo queue/session và không sai state.

## File liên quan

- `core/session.py`: state machine, AEC gate, queue pipeline, cancellation, echo guard.
- `core/protocol.py`: `tts:stop` với `interrupt=true`.
- `config/settings.py`: cấu hình Parakeet/Silero và VAD 450 ms.
- `core/providers/asr/parakeet_silero.py`: Silero VAD + Parakeet local ASR.
- `server.py`: preload Parakeet trước khi nhận client.
- `patches/xiaozhi-esp32-barge-in.patch`: thay đổi cần áp cho firmware Xiaozhi tham khảo.
