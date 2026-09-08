# Kiến Trúc Hệ Thống & Kế Hoạch Voice Pipeline

VeeTee phát triển theo hướng **server-only**: ESP32/Xiaozhi dùng firmware nguyên bản, server chịu trách nhiệm tối ưu latency, cancellation, pacing, backpressure và chống stale turn. Không yêu cầu patch firmware để dùng đường mặc định.

Plan triển khai chi tiết: [2026-09-08-server-khong-sua-fw.md](../../task-plans/2026-09-08-server-khong-sua-fw.md).

## 1. Kiến trúc hiện tại

```text
ESP32 stock / Web Client
        |
        | Opus 16 kHz hoặc PCM16 16 kHz
        v
Silero VAD (450 ms end silence)
        |
        v
Parakeet CTC 0.6B Vietnamese
        |
        | final transcript
        v
LLM streaming
        |
        | asyncio.Queue(maxsize=3)
        v
VieNeu v3 Turbo TTS
        |
        | bounded chunk queue
        v
AudioPacer (120 ms send-ahead mặc định)
        |
        | Opus 24 kHz / 60 ms baseline
        v
ESP32 stock speaker
```

Deepgram được giữ làm ASR dự phòng. TTS dùng engine chung và xử lý từng clause tuần tự; queue cho phép LLM sinh trước clause tiếp theo nhưng không chạy nhiều TTS inference song song trên cùng engine.

## 2. Các tối ưu đã triển khai

### VAD endpointing

- `min_silence_duration_ms`: 650 -> 450 ms.
- Silero dùng frame 32 ms nên threshold 450 ms được vượt ở khoảng 480 ms.
- So với cấu hình 650 ms trước đây, endpointing giảm khoảng 192 ms theo phép tính frame.
- Đây chưa phải số đo end-to-end trên ESP32 thật.

### Turn lifecycle và cancellation

- Turn có generation/ownership; turn cũ không được gửi audio/stop hoặc hoàn tất dialogue sau khi đã bị thay thế.
- Capture ASR có generation riêng; callback transcript cũ đến sau abort/listen-start/close bị loại bỏ.
- Producer clause không block trong `finally` khi consumer đã bị hủy và queue đầy.
- `abort` và explicit `listen:start` là đường ngắt chuẩn của firmware stock.

### Audio pacing

- Bỏ fixed `sleep(0.005)` giữa các frame.
- `AudioPacer` dùng monotonic clock và duration frame để giữ lượng audio gửi trước trong budget cấu hình.
- Mặc định `tts.send_ahead_ms=120`, tương đương khoảng 2 frame 60 ms.
- Sau network stall, pacer không burst bù vượt budget.
- Cancellation có thể cắt chờ pacing ngay.

### TTS backpressure

- Bridge thread -> async của VieNeu dùng queue bounded, mặc định `tts.stream_queue_max_chunks=4`.
- Worker bị chặn bởi queue được giải phóng khi turn cancel.
- Inference GPU/native đang chạy chỉ dừng khi engine trả quyền điều khiển; không cam kết preempt tức thời.

## 3. Barge-in trên firmware nguyên bản

Policy mặc định là `client_only`.

- Server không phụ thuộc `device_aec`.
- Server không gửi extension `interrupt=true`.
- Khi nhận `abort` hoặc `listen:start` trong lúc speaking, server cancel turn và gửi `{"type":"tts","state":"stop"}` chuẩn.
- Automatic VAD speech-start barge-in trong lúc loa phát đang tắt mặc định để tránh robot tự nghe tiếng của chính nó.
- `features.aec=true` hoặc `mode=realtime` không được coi là bằng chứng device-side AEC hoạt động.

Firmware stock không có lệnh flush/ACK playback chung cho protocol đang dùng. Vì vậy server giảm audio còn nằm phía client bằng send-ahead pacing, nhưng **không thể suy ra từ server log rằng loa đã dừng ngay**.

## 4. Protocol/audio compatibility

- Hello stock được chấp nhận khi thiếu `features`, có `{mcp:true}`, hoặc có `{aec:true}`.
- Opus input 16 kHz và TTS output 24 kHz giữ baseline 60 ms.
- Input frame duration được tách khỏi output TTS frame duration.
- Binary protocol V1/V2/V3 có regression cho round-trip/truncated packet.
- Web diagnostic PCM16 vẫn được giữ.

## 5. Kiểm thử hiện tại

Regression hiện có bao phủ:

- stock hello/protocol V1/V2/V3;
- queue full + cancel/no deadlock;
- normal queue finish và producer error;
- abort/listen-start state;
- stale ASR callback;
- realtime playback-tail echo guard;
- network send stall;
- two-session ownership isolation;
- TTS bounded backpressure;
- E2E contract thiếu marker/thứ tự sai phải fail.

Kết quả gần nhất trước bước tài liệu: **26/26 unit tests PASS**, `py_compile` và `git diff --check` PASS. Runtime E2E với model thật và board ESP32 vẫn là bài kiểm tra có điều kiện môi trường.

## 6. Nghiệm thu còn lại

Phần server sẽ được coi là sẵn sàng cho test stock FW khi regression tiếp tục pass và tài liệu đồng bộ. Nghiệm thu phần cứng cần firmware nguyên bản đang có trên board:

1. Kết nối OTA/WebSocket và xác nhận hello/audio hai chiều.
2. Hội thoại nhiều lượt và normal tail không bị cắt.
3. Ngắt bằng nút/wake word nếu board/FW hỗ trợ, quan sát `abort`/`listen:start` chuẩn.
4. Đo request-abort -> last binary server gửi.
5. Đo thao tác/ngắt -> loa thật sự dừng bằng quan sát/ghi âm có timestamp.
6. Lặp tối thiểu 20 lượt normal và 20 lượt interrupt, báo lỗi và p50/p95.

Nếu chưa có board, hardware giữ trạng thái `PENDING`; không cần `idf.py`, build hay flash FW tùy biến để nghiệm thu phần server.

## 7. Artifact patch cũ

`patches/xiaozhi-esp32-barge-in.patch` được giữ lại để truy vết thử nghiệm trước đây. Nó **không phải yêu cầu** của đường hỗ trợ hiện tại và không được tự áp vào firmware tham khảo.
