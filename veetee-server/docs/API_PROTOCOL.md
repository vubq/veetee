# VeeTee Protocol Contract

Cập nhật: **2026-09-18**

Tài liệu này mô tả contract wire giữa VeeTee và ESP32/Xiaozhi firmware nguyên bản, cộng các HTTP management/browser endpoint riêng. AI intent/memory/tool reasoning nội bộ được mô tả tại [ARCHITECTURE.md](ARCHITECTURE.md), không phải capability mới mà ESP32 phải hiểu.

## 1. WebSocket connection

Endpoint mặc định:

```text
ws://<server>:8000/
```

Standalone WebSocket path hiện đọc các header:

| Header | Dùng cho |
| --- | --- |
| `Protocol-Version` | chọn binary V1/V2/V3; mặc định V1 |
| `Device-Id` | định danh client do client khai báo |
| `Client-Id` | định danh client do client khai báo |

Hai listener WS đều yêu cầu xác thực trước khi tạo session/ASR. Firmware stock dùng credential **riêng theo thiết bị** do server cấp sau quy trình OTA pairing; credential đó được trả trong `websocket.token`, rồi firmware tự gửi `Authorization: Bearer <device-credential>` cùng `Device-Id` và `Client-Id` khi mở WebSocket. Không còn shared `VEETEE_WS_TOKEN`/`server.ws_token`.

Pairing stock: thiết bị gọi `GET/POST /ota/` với `Device-Id`, `Client-Id`, `Activation-Version`; khi chưa bind, server trả `activation.code` 6 số. Người quản trị nhập mã vào dashboard, chọn assistant và có thể gán memory owner. Firmware poll `POST /ota/activate`; với Activation-Version 1 body có thể là `{}` và server trả `202` cho đến khi được duyệt, sau đó `200`. Lần OTA kế tiếp phát `websocket.url`, `version` và token riêng **một lần**; firmware persist token. Các OTA check thường lệ sau đó không re-disclose credential. Re-pair rotate credential mới; revoke làm credential mất hiệu lực.

Browser quản trị đăng nhập bằng `POST /api/session` với `Authorization: Bearer <VEETEE_MANAGEMENT_TOKEN>`, nhận cookie HttpOnly/SameSite=Strict có hạn một giờ, rồi gọi management API và mở `/ws` cùng origin bằng cookie. Dashboard không lưu management token trong URL/localStorage/sessionStorage. Dùng HTTPS khi kết nối từ xa. Nếu terminate TLS ở proxy, bảo đảm cookie Secure được xử lý đúng; server không tin tùy tiện header forwarded ngoài logic URL OTA đã giới hạn.

Origin khác host bị từ chối trừ khi nằm trong `server.ws_allowed_origins`. Client stock không có Origin vẫn phải xác thực. Hai listener chia sẻ `ws_max_sessions` (mặc định 8), kể cả phiên đang chờ hello. Client phải gửi hello hợp lệ trong `ws_hello_timeout_seconds` (5 giây), trước mọi chat/audio; frame tối đa 64 KiB.

WebSocket auth cách ly theo device credential và assistant binding. Durable memory cũng hỗ trợ `owner_id` riêng trên paired device; session đã xác thực ưu tiên owner này và fallback `memory.trusted_owner_id` cho thiết bị legacy/chưa gán. Không tự suy owner từ Device-Id/Client-Id.

Hello stock có thể không có `features`:

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

Firmware có thể quảng bá capability stock, ví dụ:

```json
{
  "type": "hello",
  "features": {
    "mcp": true,
    "aec": true
  }
}
```

Server trả session/audio output:

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "<session-id>",
  "audio_params": {
    "format": "opus",
    "sample_rate": 24000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

`features.aec=true` không phải bằng chứng automatic barge-in có thể bật an toàn. Server hiện chưa có server-side AEC hoàn chỉnh.

## 2. Binary audio

### V1

- Client -> server: raw Opus, baseline 16 kHz mono, 60 ms/frame.
- Server -> client: raw Opus, baseline 24 kHz mono, 60 ms/frame.
- Web diagnostic client có thể dùng `pcm16`; đây là browser/diagnostic extension, không phải yêu cầu cho firmware stock.

### V2

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

### V3

```c
struct BinaryProtocol3 {
    uint8_t  type;
    uint8_t  reserved;
    uint16_t payload_size;
    uint8_t  payload[];
} __attribute__((packed));
```

Server validate header/payload size và bỏ packet truncated.

## 3. Client -> server JSON

### `listen:start`

```json
{"type":"listen","state":"start","mode":"realtime"}
```

`mode` có thể là `realtime`, `auto` hoặc `manual`. Server ghi nhận mode client báo; không ép firmware đổi mode/AEC. Nếu event đến lúc server đang nói, VeeTee cancel turn hiện tại và gửi `tts:stop` chuẩn.

### `listen:detect`

```json
{"type":"listen","state":"detect","text":"VeeTee ơi"}
```

Nếu có text, server có thể chuyển chính text đó vào AI turn sau cửa sổ phối hợp `listen:start`. Không dùng exact wake/exit matcher để route semantic intent.

Firmware không gửi `listen:detect` vẫn có thể hội thoại qua mic/ASR; server không tạo synthetic wake text từ `hello` hay `listen:start`.

### `listen:stop`

```json
{"type":"listen","state":"stop"}
```

Server finalize utterance hiện tại thay vì bắt buộc chờ thêm VAD silence.

### `abort`

```json
{"type":"abort","reason":"wake_word_detected"}
```

Server cancel turn, invalidate capture generation cũ, ngừng gửi audio mới và gửi `tts:stop` nếu cần.

## 4. Server -> client JSON

### STT

```json
{
  "type": "stt",
  "text": "Hà Nội là thủ đô của nước nào?",
  "is_final": true,
  "speech_final": true
}
```

### VAD

```json
{"type":"vad","state":"speech_started"}
{"type":"vad","state":"speech_ended"}
```

### TTS

```json
{"type":"tts","state":"start"}
{"type":"tts","state":"sentence_start","text":"Hà Nội là thủ đô của Việt Nam."}
```

Sau binary audio cuối hoặc khi cancel:

```json
{"type":"tts","state":"stop"}
```

Đường stock không cần field `interrupt=true` hay decoder-flush extension riêng.

Server có thể cache raw Opus cho nội dung đã được AI sinh/chọn và đóng gói lại theo protocol version của session. Cache là tối ưu delivery, không biến literal tool result thành semantic reply đúng persona.

## 5. MCP stock

Device MCP dùng wrapper `type=mcp` với JSON-RPC 2.0. Baseline flow hiện tại:

```text
initialize
-> tools/list (withUserTools=false, có pagination)
-> tools/call
```

Request ID là numeric và baseline protocol version là `2024-11-05`. Nếu board không quảng bá `features.mcp` hoặc discovery lỗi, chat thường vẫn hoạt động.

MCP capability quyết định tool nào có thể expose; semantic decision có gọi tool hay không vẫn do AI phía server quyết định.

## 6. Barge-in và pacing

Policy mặc định:

```text
server.barge_in_policy = client_only
```

- stock `abort` và explicit `listen:start` có thể cancel turn;
- speech-start trong lúc TTS phát không tự cancel theo policy mặc định;
- `features.aec=true` hoặc `mode=realtime` không tự bật automatic speech barge-in;
- TTS pacing giới hạn audio gửi trước để giảm tail.

Stock firmware không cung cấp playback queue depth/flush ACK chung. `tts:stop` hay last binary ở server không tương đương physical speaker stop.

## 7. HTTP OTA, dashboard và management

HTTP mặc định ở `:8003`.

Public/stock-facing routes gồm:

```text
GET/POST /ota/
GET/POST /api/ota/
GET /   (dashboard/static UI)
```

Management routes chính:

```text
POST   /api/session
GET    /api/assistants
POST   /api/assistants
PATCH  /api/assistants/{assistant_id}
DELETE /api/assistants/{assistant_id}
GET    /api/devices
GET    /api/devices/pending
POST   /api/devices/pair
PATCH  /api/devices/{device_id}/{client_id}
POST   /api/devices/{device_id}/{client_id}/revoke
GET    /api/runtime-config
PATCH  /api/runtime-config
GET    /api/diagnostics
GET    /api/prompt
POST   /api/prompt
POST   /api/test-voice
```

Management routes yêu cầu `management.token` hoặc `VEETEE_MANAGEMENT_TOKEN`. API vẫn chấp nhận header management trực tiếp cho automation, nhưng dashboard mặc định đổi token lấy cookie HttpOnly qua `/api/session` rồi dùng cookie. Token trống làm management access bị từ chối.

Management credential và per-device WebSocket credential là hai lớp riêng; firmware stock không cần biết management token.
