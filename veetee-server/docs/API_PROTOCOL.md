# Đặc Tả Giao Thức VeeTee Protocol (Protocol Specification)

Tài liệu này mô tả chi tiết giao thức giao tiếp giữa thiết bị phần cứng (ESP32) và VeeTee Server.

---

## 1. WebSocket Handshake & Headers

Khi thiết lập kết nối WebSocket tới `ws://<server>:8000/`, client gửi các header:

| Header | Mô tả | Ví dụ |
| :--- | :--- | :--- |
| `Authorization` | Token xác thực (tùy chọn) | `Bearer my-token` |
| `Protocol-Version`| Phiên bản giao thức | `1`, `2`, hoặc `3` |
| `Device-Id` | Địa chỉ MAC phần cứng của ESP32 | `84:F7:03:12:34:56` |
| `Client-Id` | UUID của thiết bị | `550e8400-e29b-41d4-a716-446655440000` |

---

## 2. Giao Thức Nhị Phân (Binary Audio Protocol)

### 2.1 Phiên bản 1 (Version 1 - Mặc định)
- Dữ liệu binary là gói Opus thuần túy (Raw Opus Frame) không có phần header bổ sung.
- Chiều Client -> Server: 16000Hz mono, 60ms/frame (960 samples).
- Chiều Server -> Client: 24000Hz mono, 60ms/frame (1440 samples).

### 2.2 Phiên bản 2 (Version 2)
```c
struct BinaryProtocol2 {
    uint16_t version;        // 2 (Big Endian)
    uint16_t type;           // 0: OPUS, 1: JSON
    uint32_t reserved;       // 0
    uint32_t timestamp;      // Timestamp mili-giây
    uint32_t payload_size;   // Kích thước payload (bytes)
    uint8_t  payload[];      // Dữ liệu Opus
} __attribute__((packed));
```

### 2.3 Phiên bản 3 (Version 3)
```c
struct BinaryProtocol3 {
    uint8_t  type;           // 0: OPUS, 1: JSON
    uint8_t  reserved;       // 0
    uint16_t payload_size;   // Kích thước payload (bytes)
    uint8_t  payload[];      // Dữ liệu Opus
} __attribute__((packed));
```

---

## 3. Các Loại Tin Nhắn JSON (Text Frames)

### 3.1 Client -> Server

#### a) Hello Handshake
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

#### b) Listen (Bắt đầu / Dừng thu âm)
```json
{
  "session_id": "xxx",
  "type": "listen",
  "state": "start",
  "mode": "auto"
}
```
Hoặc khi phát hiện từ khóa đánh thức:
```json
{
  "session_id": "xxx",
  "type": "listen",
  "state": "detect",
  "text": "Hey VeeTee"
}
```
Hoặc khi dừng thu âm:
```json
{
  "session_id": "xxx",
  "type": "listen",
  "state": "stop"
}
```

#### c) Abort (Ngắt câu trả lời / Hủy lượt)
```json
{
  "session_id": "xxx",
  "type": "abort",
  "reason": "wake_word_detected"
}
```

---

### 3.2 Server -> Client

#### a) Hello Handshake Acknowledge
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

#### b) STT Result (Văn bản nhận diện giọng nói)
```json
{
  "session_id": "xxx",
  "type": "stt",
  "text": "Hà Nội là thủ đô của nước nào?"
}
```

#### c) LLM Emotion / Display State
```json
{
  "session_id": "xxx",
  "type": "llm",
  "emotion": "happy",
  "text": "😊"
}
```
*(Các cảm xúc hỗ trợ: `happy`, `neutral`, `surprised`, `sad`, `thinking`, `angry`, `relaxed`)*

#### d) TTS State & Subtitles
Bắt đầu phát TTS:
```json
{
  "session_id": "xxx",
  "type": "tts",
  "state": "start"
}
```
Hiển thị câu đang nói (subtitles):
```json
{
  "session_id": "xxx",
  "type": "tts",
  "state": "sentence_start",
  "text": "Hà Nội là thủ đô của Việt Nam."
}
```
Kết thúc phát TTS:
```json
{
  "session_id": "xxx",
  "type": "tts",
  "state": "stop"
}
```
