# Kiến Trúc Hệ Thống & Kế Hoạch Voice Pipeline

Dự án **VeeTee Realtime Server** phục vụ ESP32/Xiaozhi và Web Client qua WebSocket, với mục tiêu phản hồi nhanh, stream toàn trình và giữ tương thích với firmware Xiaozhi tham khảo.

## 1. Kiến trúc hiện tại

```text
ESP32 / Web Client
        |
        | Opus 16 kHz hoặc PCM16 16 kHz
        v
Silero VAD local
        |
        | speech start / speech end
        v
Parakeet CTC 0.6B Vietnamese
        |
        | final transcript
        v
OmniRoute / Groq LLM streaming
        |
        | bounded clause queue (maxsize=3)
        v
VieNeu v3 Turbo local TTS
        |
        | Opus 24 kHz + tts state
        v
ESP32 speaker
```

ASR mặc định hiện tại là **NVIDIA Parakeet CTC 0.6B Vietnamese + Silero VAD local**. Deepgram vẫn tồn tại như provider dự phòng nhưng không còn là đường mặc định.

## 2. Các tối ưu đã hoàn thành

### VAD endpointing

- `min_silence_duration_ms`: **650 ms -> 450 ms**.
- Silero xử lý frame 512 samples ở 16 kHz, tương đương **32 ms/frame**.
- Vì ngưỡng chỉ được kiểm tra theo frame, 450 ms thực tế được vượt ở khoảng **480 ms**.
- So với cấu hình 650 ms trước đây, điểm kết thúc câu thực tế giảm từ khoảng 672 ms xuống 480 ms, tiết kiệm khoảng **192 ms** sau khi người dùng ngừng nói.

### LLM -> TTS pipeline

- LLM producer và TTS consumer chạy chồng lấp qua `asyncio.Queue(maxsize=3)`.
- Khi TTS đang xử lý câu hiện tại, LLM có thể tiếp tục sinh sẵn các câu tiếp theo.
- Producer cancellation không còn `await queue.put()` trong `finally`, tránh deadlock khi queue đầy và consumer đã bị hủy.
- TTS vẫn tổng hợp từng câu tuần tự, nên queue giảm khoảng nghỉ giữa câu nhưng chưa biến TTS thành xử lý song song nhiều câu.

### Barge-in tương thích Xiaozhi

- `tts:stop` bình thường chỉ báo kết thúc lượt phát, để firmware phát hết audio đã đệm.
- Barge-in/abort dùng `{"type":"tts","state":"stop","interrupt":true}` để firmware xóa decoder/playback buffer ngay.
- `abort` hủy turn hiện tại, xóa bookkeeping ASR/VAD và cập nhật state về `LISTENING` cho `auto/realtime`, hoặc `IDLE` cho `manual`.
- Server chỉ tự động barge-in bằng VAD khi thiết bị đang ở `mode=realtime` **và** hello xác nhận `features.device_aec=true`.
- Nếu chỉ có `features.aec=true`, đó là yêu cầu server-side AEC. VeeTee hiện chưa có server-side AEC nên không bật auto barge-in từ realtime ASR cho trường hợp này.
- Echo guard tiếp tục giữ transcript phát ra từ loa khỏi trở thành lượt người dùng mới khi AEC chưa được xác nhận.

## 3. Firmware Xiaozhi tham khảo

Các thay đổi tương thích firmware đã được chuẩn bị trên source tham khảo:

- `main/application.cc`: khi nhận `tts:stop` với `interrupt=true`, gọi `audio_service_.ResetDecoder()` trước khi chuyển state.
- `main/protocols/websocket_protocol.cc`: hello quảng bá `device_aec=true` khi AEC chạy trên thiết bị; `aec=true` khi AEC được chọn ở phía server.
- `main/protocols/mqtt_protocol.cc`: cùng semantics AEC như WebSocket.

Patch có thể áp dụng lên source Xiaozhi tham khảo được lưu tại `patches/xiaozhi-esp32-barge-in.patch`.

## 4. Trạng thái kiểm thử

Các kiểm tra mô phỏng/local đã đạt:

- `queue_cancel=ok`
- `queue_normal_full=ok`
- `abort_state=ok`
- `aec_gate=ok`
- `echo_tail_guard=ok`
- `tts_stop_semantics=ok`
- Python compile check: đạt
- `git diff --check`: đạt

Chưa chạy full ESP-IDF build vì môi trường hiện tại không có `idf.py`.

## 5. Việc còn lại trước khi coi barge-in ổn định trên thiết bị thật

1. Build/flash firmware Xiaozhi có patch nói trên.
2. Xác nhận board thực sự chạy **device-side AEC** và hello gửi `features.device_aec=true`.
3. Test người dùng nói chen khi loa đang phát ở nhiều mức âm lượng và khoảng cách mic khác nhau.
4. Xác nhận `interrupt=true` dừng loa ngay, không còn audio buffer cũ phát tiếp.
5. Đo false-trigger do echo và điều chỉnh ngưỡng Silero nếu cần.
6. Đo latency thật từ speech end -> first TTS audio trên ESP32.

Chi tiết tiến độ và checklist nghiệm thu được theo dõi tại [VOICE_PIPELINE_STATUS.md](VOICE_PIPELINE_STATUS.md).
