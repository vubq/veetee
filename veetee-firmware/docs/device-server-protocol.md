# Giao thuc thiet bi-server

## Trang thai tai lieu

Day la ban tom tat wire protocol quan sat trong source tham khao. Neu Veetee ke thua
giao thuc, can tao dac ta versioned va test contract rieng; khong nen phu thuoc vao tai
lieu upstream ma khong pin commit.

## Lop semantic chung

Ca WebSocket va MQTT/UDP chia se cac semantic JSON:

| Huong | `type` | Y nghia |
| --- | --- | --- |
| Device -> server | `hello` | Cong bo version, feature va audio params |
| Device -> server | `listen` | `start`, `stop`, `detect`; mode auto/manual/realtime |
| Device -> server | `abort` | Dung TTS/phien hien tai |
| Hai chieu | `mcp` | JSON-RPC 2.0 cho tool discovery/call |
| Server -> device | `stt` | Text nhan dang tu giong noi |
| Server -> device | `llm` | Emotion/text de cap nhat UI |
| Server -> device | `tts` | `start`, `sentence_start`, `stop` |
| Server -> device | `system` | Lenh he thong; upstream ho tro `reboot` |
| Server -> device | `alert` | Status, message va emotion |
| Hai chieu | `goodbye` | Ket thuc audio channel, tuy transport |

Moi message sau handshake nen mang `session_id` de tranh tron phien.

## WebSocket

### Handshake HTTP

Header quan sat:

- `Authorization: Bearer <token>`
- `Protocol-Version`
- `Device-Id`: thuong la MAC vat ly
- `Client-Id`: UUID phan mem, co the doi khi xoa NVS

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

Server phan hoi `type=hello`, `transport=websocket`, `session_id` va audio params. Neu
khong co hello hop le trong khoang 10 giay theo implementation tham khao, open that bai.

### Binary frame

- Version 1: raw Opus payload.
- Version 2: header packed gom version, type, reserved, timestamp 32-bit,
  payload size 32-bit, sau do payload.
- Version 3: header nho gom type 8-bit, reserved 8-bit, payload size 16-bit.

Can quy dinh ro byte order khi viet implementation moi; viec copy C struct packed truc
tiep giua kien truc la rui ro neu dac ta khong chot endianness.

## MQTT control va UDP audio

MQTT mang hello/control JSON. Server tra ve endpoint UDP va session key:

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

UDP packet tham khao:

```text
| type 1B | flags 1B | payload_len 2B | ssrc 4B |
| timestamp 4B | sequence 4B | encrypted Opus payload |
```

- Header so nguyen dung network byte order theo tai lieu upstream.
- Audio payload ma hoa AES-CTR 128-bit.
- Counter duoc tao tu timestamp va sequence.
- Packet sequence cu bi drop; gap nho duoc canh bao nhung van chap nhan.
- MQTT co reconnect; UDP can thuong luong lai khi mat channel.

AES-CTR chi ma hoa, khong tu cung cap integrity/authentication cho tung packet. Neu
Veetee dung UDP, nen danh gia AEAD, key rotation, nonce uniqueness va replay window.

## MCP tren transport

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

Flow chinh:

1. Device hello cong bo `features.mcp=true`.
2. Server gui `initialize`; device tra protocol version va server info.
3. Server gui `tools/list`, lap theo `nextCursor` neu co.
4. Server gui `tools/call`; device tra `result.content` hoac JSON-RPC `error`.
5. Device co the gui notification khong co `id`.

Method quan sat: `initialize`, `tools/list`, `tools/call`. Tool schema theo JSON Schema
object don gian. `withUserTools=true` mo rong danh sach tool dac quyen.

## Yeu cau contract neu ap dung cho Veetee

- Version moi wire format va policy tuong thich ro rang.
- Gioi han kich thuoc JSON, binary frame, MCP arguments va image base64.
- Xac thuc device, rang buoc token voi `Device-Id`/`Client-Id`.
- Validate `session_id`, message type, enum, sample rate va payload length.
- Timeout, ping/pong, reconnect, duplicate va out-of-order behavior.
- TLS cho WebSocket/MQTT; khong phan phoi UDP key qua kenh khong ma hoa.
- Authorization rieng cho tool AI va user-only tool.

## Source doi chieu

- `../references/xiaozhi-esp32/docs/websocket.md`
- `../references/xiaozhi-esp32/docs/mqtt-udp.md`
- `../references/xiaozhi-esp32/docs/mcp-protocol.md`
- `../references/xiaozhi-esp32/main/protocols/`
- `../references/xiaozhi-esp32/main/mcp_server.cc`
