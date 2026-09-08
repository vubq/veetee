# Đặc Tả Giao Thức VeeTee Protocol

Tài liệu này mô tả đường tương thích mặc định giữa VeeTee Server và ESP32/Xiaozhi firmware nguyên bản. Server không yêu cầu extension riêng trong hello hoặc TTS stop.

## 1. WebSocket handshake

Client kết nối tới `ws://<server>:8000/` và có thể gửi các header chuẩn đang hỗ trợ:

| Header | Mô tả | Ví dụ |
| :--- | :--- | :--- |
| `Authorization` | Token nếu server cấu hình auth | `Bearer my-token` |
| `Protocol-Version` | Binary protocol | `1`, `2`, `3` |
| `Device-Id` | ID/MAC thiết bị | `84:F7:03:12:34:56` |
| `Client-Id` | UUID client | `550e8400-e29b-41d4-a716-446655440000` |

Hello firmware stock có thể không có `features`:

```json
{
  "type": "hello",
  "version": 1,
  "transport": "websocket",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

Hoặc có các cờ firmware nguyên bản hỗ trợ, ví dụ:

```json
{
  "type": "hello",
  "features": {
    "mcp": true,
    "aec": true
  }
}
```

`features.aec=true` biểu thị firmware chọn hướng server-side AEC. VeeTee hiện chưa có server-side AEC hoàn chỉnh, nên cờ này không tự bật automatic speech barge-in. `mode=realtime` cũng không được dùng như bằng chứng AEC hiệu quả.

Server phản hồi hello với session và audio output:

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "9c180bb6204e47f0b498bb5bb68773b5",
  "audio_params": {
    "format": "opus",
    "sample_rate": 24000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

## 2. Audio binary

### Version 1

- Client -> Server: raw Opus 16 kHz mono, baseline 60 ms/frame.
- Server -> Client: raw Opus 24 kHz mono, baseline 60 ms/frame.
- Web diagnostic client có thể khai báo `audio_params.format=pcm16` để gửi PCM16 16 kHz.

### Version 2

```c
struct BinaryProtocol2 {
    uint16_t version;
    uint16_t type;
    uint32_t reserved;
    uint32_t timestamp;
    uint32_t payload_size;
    uint8_t  payload[];
} __attribute__((packed));
```

### Version 3

```c
struct BinaryProtocol3 {
    uint8_t  type;
    uint8_t  reserved;
    uint16_t payload_size;
    uint8_t  payload[];
} __attribute__((packed));
```

Server validate packet size/header và bỏ packet truncated thay vì đọc payload không đầy đủ.

## 3. Client -> Server JSON

### Listen start

```json
{
  "session_id": "xxx",
  "type": "listen",
  "state": "start",
  "mode": "realtime"
}
```

`mode` có thể là `realtime`, `auto` hoặc `manual`. Server ghi nhận mode client báo, không tự ép firmware đổi mode.

Nếu `listen:start` đến khi server đang speaking, VeeTee coi đây là yêu cầu ngắt turn hiện tại: cancel LLM/TTS, invalidate capture cũ và gửi `tts:stop` chuẩn.

### Listen detect

```json
{
  "session_id": "xxx",
  "type": "listen",
  "state": "detect",
  "text": "Hey VeeTee"
}
```

### Listen stop

```json
{
  "session_id": "xxx",
  "type": "listen",
  "state": "stop"
}
```

Server finalize utterance hiện tại để không phải chờ thêm VAD silence khi client đã chủ động kết thúc capture.

### Abort

```json
{
  "session_id": "xxx",
  "type": "abort",
  "reason": "wake_word_detected"
}
```

Khi nhận `abort`, server cancel turn, invalidate capture generation, reset bookkeeping liên quan và gửi `tts:stop` chuẩn nếu đang phát. State được đưa về listening/idle theo listening mode hiện tại.

## 4. Server -> Client JSON

### STT

```json
{
  "session_id": "xxx",
  "type": "stt",
  "text": "Hà Nội là thủ đô của nước nào?",
  "is_final": true,
  "speech_final": true
}
```

### VAD

```json
{"session_id":"xxx","type":"vad","state":"speech_started"}
```

```json
{"session_id":"xxx","type":"vad","state":"speech_ended"}
```

### TTS

```json
{"session_id":"xxx","type":"tts","state":"start"}
```

```json
{
  "session_id": "xxx",
  "type": "tts",
  "state": "sentence_start",
  "text": "Hà Nội là thủ đô của Việt Nam."
}
```

Sau binary audio cuối của turn bình thường:

```json
{"session_id":"xxx","type":"tts","state":"stop"}
```

Đường hỗ trợ stock FW dùng cùng message `tts:stop` chuẩn khi client yêu cầu ngắt. Server dừng gửi audio mới càng sớm càng tốt; không phụ thuộc trường extension riêng để flush decoder.

## 5. Barge-in policy

Mặc định:

```text
server.barge_in_policy = client_only
```

Quy tắc:

- `abort` và explicit `listen:start` từ client có thể ngắt turn đang nói.
- VAD speech-start trong lúc TTS phát không tự động cancel turn dưới policy mặc định.
- Echo guard kéo dài qua estimated playback tail để transcript do loa lọt vào mic không tạo turn mới.
- Automatic speech barge-in chỉ có thể được xem xét sau này bằng profile thiết bị đã xác minh AEC độc lập; không suy ra từ hello `aec=true` hoặc `mode=realtime`.

## 6. Pacing và giới hạn protocol

TTS output được pace theo duration frame bằng monotonic clock. `tts.send_ahead_ms` mặc định 120 ms giới hạn lượng audio server gửi trước theo mô hình playback estimate.

Firmware stock không cung cấp playback queue depth hoặc flush ACK chung. Vì vậy:

- `tts:stop`/last binary đo được ở server không tương đương thời điểm loa vật lý dừng;
- send-ahead 120 ms là giá trị khởi đầu để test board, không phải cam kết latency;
- cần đo trên ESP32 thật trước khi tune xuống thấp hơn hoặc cao hơn.
