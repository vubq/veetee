# Giao thức thiết bị-server

## Trạng thái tài liệu

Đây là bản tóm tắt wire protocol quan sát trong source tham khảo. Nếu Veetee kế thừa
giao thức, cần tạo đặc tả versioned và test contract riêng; không nên phụ thuộc vào tài
liệu upstream mà không pin commit.

## Lớp semantic chung

Cả WebSocket và MQTT/UDP chia sẻ các semantic JSON:

| Hướng | `type` | Ý nghĩa |
| --- | --- | --- |
| Device -> server | `hello` | Công bố version, feature và audio params |
| Device -> server | `listen` | `start`, `stop`, `detect`; mode auto/manual/realtime |
| Device -> server | `abort` | Dừng TTS/phiên hiện tại |
| Hai chiều | `mcp` | JSON-RPC 2.0 cho tool discovery/call |
| Server -> device | `stt` | Text nhận dạng từ giọng nói |
| Server -> device | `llm` | Emotion/text để cập nhật UI |
| Server -> device | `tts` | `start`, `sentence_start`, `stop` |
| Server -> device | `system` | Lệnh hệ thống; upstream hỗ trợ `reboot` |
| Server -> device | `alert` | Status, message và emotion |
| Hai chiều | `goodbye` | Kết thúc audio channel, tùy transport |

Mỗi message sau handshake nên mang `session_id` để tránh trộn phiên.

## WebSocket

### Handshake HTTP

Header quan sát:

- `Authorization: Bearer <token>`
- `Protocol-Version`
- `Device-Id`: thường là MAC vật lý
- `Client-Id`: UUID phần mềm, có thể đổi khi xóa NVS

### Hello

```json
{
  "type": "hello",
  "version": 1,
  "features": { "mcp": true, "aec": true },
  "transport": "websocket",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

Server phản hồi `type=hello`, `transport=websocket`, `session_id` và audio params. Nếu
không có hello hợp lệ trong khoảng 10 giây theo implementation tham khảo, open thất bại.

### Binary frame

- Version 1: raw Opus payload.
- Version 2: header packed gồm version, type, reserved, timestamp 32-bit,
  payload size 32-bit, sau đó payload.
- Version 3: header nhỏ gồm type 8-bit, reserved 8-bit, payload size 16-bit.

Cần quy định rõ byte order khi viết implementation mới; việc copy C struct packed trực
tiếp giữa kiến trúc là rủi ro nếu đặc tả không chốt endianness.

## MQTT control và UDP audio

MQTT mang hello/control JSON. Server trả về endpoint UDP và session key:

```json
{
  "type": "hello",
  "transport": "udp",
  "session_id": "...",
  "audio_params": { "format": "opus", "sample_rate": 24000, "channels": 1, "frame_duration": 60 },
  "udp": {
    "server": "host",
    "port": 8888,
    "key": "hex-encoded AES key",
    "nonce": "hex-encoded nonce"
  }
}
```

UDP packet tham khảo:

```text
| type 1B | flags 1B | payload_len 2B | ssrc 4B |
| timestamp 4B | sequence 4B | encrypted Opus payload |
```

- Header số nguyên dùng network byte order theo tài liệu upstream.
- Audio payload mã hóa AES-CTR 128-bit.
- Counter được tạo từ timestamp và sequence.
- Packet sequence cũ bị drop; gap nhỏ được cảnh báo nhưng vẫn chấp nhận.
- MQTT có reconnect; UDP cần thương lượng lại khi mất channel.

AES-CTR chỉ mã hóa, không tự cung cấp integrity/authentication cho từng packet. Nếu
Veetee dùng UDP, nên đánh giá AEAD, key rotation, nonce uniqueness và replay window.

## MCP trên transport

Outer envelope:

```json
{
  "session_id": "...",
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": { "name": "self.audio_speaker.set_volume", "arguments": { "volume": 50 } },
    "id": 3
  }
}
```

Flow chính:

1. Device hello công bố `features.mcp=true`.
2. Server gửi `initialize`; device trả protocol version và server info.
3. Server gửi `tools/list`, lặp theo `nextCursor` nếu có.
4. Server gửi `tools/call`; device trả `result.content` hoặc JSON-RPC `error`.
5. Device có thể gửi notification không có `id`.

Method quan sát: `initialize`, `tools/list`, `tools/call`. Tool schema theo JSON Schema
object đơn giản. `withUserTools=true` mở rộng danh sách tool đặc quyền.

## Yêu cầu contract nếu áp dụng cho Veetee

- Version mới wire format và policy tương thích rõ ràng.
- Giới hạn kích thước JSON, binary frame, MCP arguments và image base64.
- Xác thực device, ràng buộc token với `Device-Id`/`Client-Id`.
- Validate `session_id`, message type, enum, sample rate và payload length.
- Timeout, ping/pong, reconnect, duplicate và out-of-order behavior.
- TLS cho WebSocket/MQTT; không phân phối UDP key qua kênh không mã hóa.
- Authorization riêng cho tool AI và user-only tool.

## Source đối chiếu

- `../references/xiaozhi-esp32/docs/websocket.md`
- `../references/xiaozhi-esp32/docs/mqtt-udp.md`
- `../references/xiaozhi-esp32/docs/mcp-protocol.md`
- `../references/xiaozhi-esp32/main/protocols/`
- `../references/xiaozhi-esp32/main/mcp_server.cc`
