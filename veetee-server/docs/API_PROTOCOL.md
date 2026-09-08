# Đặc Tả Giao Thức VeeTee Protocol

Tài liệu này mô tả giao thức giữa ESP32/Xiaozhi và VeeTee Server, gồm handshake, audio binary, listening mode, AEC capability và semantics ngắt TTS.

## 1. WebSocket handshake & headers

Client kết nối tới `ws://<server>:8000/` và có thể gửi:

| Header | Mô tả | Ví dụ |
| :--- | :--- | :--- |
| `Authorization` | Token xác thực nếu được cấu hình | `Bearer my-token` |
| `Protocol-Version` | Phiên bản binary protocol | `1`, `2`, `3` |
| `Device-Id` | ID/MAC thiết bị | `84:F7:03:12:34:56` |
| `Client-Id` | UUID client | `550e8400-e29b-41d4-a716-446655440000` |

Sau khi WebSocket mở, firmware gửi `hello`:

```json
{
  "type": "hello",
  "version": 1,
  "transport": "websocket",
  "features": {
    "device_aec": true,
    "mcp": true
  },
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

### AEC capability

Hai cờ AEC có ý nghĩa khác nhau:

- `features.device_aec=true`: firmware xác nhận echo cancellation đang chạy **trên thiết bị**. Đây là điều kiện VeeTee dùng để cho phép automatic realtime barge-in trong khi loa đang phát.
- `features.aec=true`: firmware chọn **server-side AEC**. VeeTee hiện chưa triển khai server-side AEC, vì vậy cờ này không đủ để bật automatic realtime barge-in.

Firmware không nên gửi đồng thời hai cờ cho cùng một AEC mode.

Server phản hồi:

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

## 2. Giao thức audio binary

### Version 1

- Client -> Server: raw Opus 16 kHz mono, mặc định 60 ms/frame.
- Server -> Client: raw Opus 24 kHz mono, mặc định 60 ms/frame.
- Web diagnostic client có thể khai báo `audio_params.format=pcm16` để gửi PCM16 16 kHz trực tiếp.

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

`mode` có thể là:

- `realtime`: mic tiếp tục hoạt động trong lúc TTS phát. Automatic VAD barge-in chỉ được bật khi `device_aec=true`.
- `auto`: luồng nghe tự động thông thường.
- `manual`: thiết bị tự điều khiển thời điểm bắt đầu/dừng capture.

Nếu `listen:start` đến trong lúc server đang `SPEAKING`, VeeTee coi đây là yêu cầu ngắt lượt hiện tại và gửi interrupting TTS stop.

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

Server finalize utterance hiện tại để không phải chờ thêm VAD silence khi thiết bị đã chủ động kết thúc capture.

### Abort

```json
{
  "session_id": "xxx",
  "type": "abort",
  "reason": "wake_word_detected"
}
```

Khi nhận `abort`, server hủy LLM/TTS turn hiện tại, gửi `tts:stop` với `interrupt=true`, reset ASR/VAD bookkeeping và đưa state về `LISTENING` cho `auto/realtime` hoặc `IDLE` cho `manual`.

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
{
  "session_id": "xxx",
  "type": "vad",
  "state": "speech_started"
}
```

```json
{
  "session_id": "xxx",
  "type": "vad",
  "state": "speech_ended"
}
```

### LLM display/emotion

```json
{
  "session_id": "xxx",
  "type": "llm",
  "emotion": "happy",
  "text": "😊"
}
```

### TTS start

```json
{
  "session_id": "xxx",
  "type": "tts",
  "state": "start"
}
```

### TTS sentence start

```json
{
  "session_id": "xxx",
  "type": "tts",
  "state": "sentence_start",
  "text": "Hà Nội là thủ đô của Việt Nam."
}
```

### TTS stop bình thường

```json
{
  "session_id": "xxx",
  "type": "tts",
  "state": "stop"
}
```

Stop bình thường đánh dấu LLM/TTS turn đã hoàn tất. Firmware Xiaozhi nên để audio đã nằm trong decoder/playback queue phát hết để không cắt mất đuôi câu.

### TTS stop do interruption

```json
{
  "session_id": "xxx",
  "type": "tts",
  "state": "stop",
  "interrupt": true
}
```

`interrupt=true` chỉ dùng cho barge-in, `abort`, hoặc `listen:start` trong lúc đang nói. Firmware tham khảo xử lý bằng `audio_service_.ResetDecoder()` để bỏ ngay audio cũ đã đệm.

## 5. Quy tắc barge-in

Automatic speech-start barge-in của server chỉ chạy khi đồng thời thỏa:

1. Session đang `SPEAKING`.
2. Listening mode là `realtime`.
3. Client hello đã xác nhận `features.device_aec=true`.

Nếu không đủ ba điều kiện trên, server giữ echo guard để tránh tiếng loa lọt vào mic tạo thành một user turn giả.
