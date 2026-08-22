# Giao thức thiết bị-server

## Hợp đồng Device WebSocket Veetee thực tế (M1.3 - Quyết định Veetee)

Endpoint chính thức: `/api/v1/devices/ws`

### Requirements & Defaults M1.3
- **Handshake Headers**:
  - `Authorization: Bearer <opaque_token>` (xác thực constant-time với token gateway)
  - `Protocol-Version: 1 | 2 | 3` (server hỗ trợ cả ba từ M1.5; version quyết định
    wire format binary audio frame của session)
  - `Device-Id: <id>` (non-empty, <= 128 ký tự)
  - `Client-Id: <id>` (non-empty, <= 128 ký tự)
- **Timeouts & Boundaries**:
  - Hello timeout: 10s (`VEETEE_HELLO_TIMEOUT_SECONDS=10.0`)
  - Idle timeout: 60s (`VEETEE_IDLE_TIMEOUT_SECONDS=60.0`)
  - Ping interval: 20s (`VEETEE_PING_INTERVAL_SECONDS=20.0`)
  - Pong timeout: 10s (`VEETEE_PONG_TIMEOUT_SECONDS=10.0`)
  - JSON max size: 16 KiB (16384 bytes) (`VEETEE_JSON_MAX_BYTES=16384`)
  - JSON max depth: 8 (`VEETEE_JSON_MAX_DEPTH=8`)
  - Binary max frame size: 64 KiB (65536 bytes) (`VEETEE_BINARY_MAX_BYTES=65536`)
- **Hello Negotiation**:
  - Uplink: `type: "hello"`, `version: 1`, `transport: "websocket"`, `audio_params: {format: "opus", sample_rate: 16000, channels: 1, frame_duration: 60}`
  - Firmware display có thể thêm `features.glyph_push` và capability bounded
    `text_font: {bundle, charset, size, bpp}`; server chấp nhận đúng schema này và vẫn từ
    chối field hello không xác định khác.
  - Downlink response: `type: "hello"`, `transport: "websocket"`, `session_id: "<opaque_uuid>"`, `audio_params: {format: "opus", sample_rate: 24000, channels: 1, frame_duration: 60}`
  - Thiết bị chỉ yêu cầu full-duplex detector khi công bố `features.aec=true` và gửi
    `listen/start` với `mode="realtime"`.
- **Close Codes & Errors**:
  - `1008`: Auth / missing header / hello timeout / schema error / session mismatch
  - `1009`: Oversized JSON (>16 KiB), binary (>64 KiB) hoặc payload khai báo vượt giới hạn
  - `1002`: Binary received before hello; binary audio frame malformed/truncated hoặc
    header version không khớp version đã thương lượng
  - `1001`: Idle timeout
  - `1000`: Goodbye normal close
  - `1012`: Graceful server shutdown
  - Safe error envelope: `{"code": "veetee_*", "message": "...", "session_id": "..."}`
- **Codec**:
  - Server chọn `VEETEE_AUDIO_CODEC=fake|native`; mode `native` dùng libopus stateful cho
    đúng uplink/downlink 60 ms và readiness fail-closed nếu thư viện không sẵn sàng.
  - Resampling khác sample rate vẫn deferred; Device MCP integration full tool call hoãn
    lại M3.

---

## Trạng thái tài liệu tham khảo (Upstream)

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
`listen/detect` có thể mang wake phrase trong `text` bounded; `abort` do wake word có thể
mang `reason="wake_word_detected"`. Server chấp nhận đúng hai field firmware này nhưng
không log nội dung wake phrase.

Trong implementation Veetee M1.6, thiết bị gửi binary audio hợp lệ trong khoảng
`listen/start` đến `listen/stop`. Sau `listen/stop`, fake pipeline server phát theo thứ tự
`stt`, `tts/start`, `tts/sentence_start`, binary audio, `tts/stop`. Binary hợp lệ ngoài
trạng thái listening bị bỏ; malformed/oversized vẫn bị đóng bằng `1002`/`1009` để không
làm yếu validation giao thức.

Từ M2.8, mode `auto` có server-side endpointing: firmware tiếp tục stream sau
`listen/start`, server dùng VAD turn-scoped phát hiện speech end và tự chuyển sang xử lý;
firmware không cần gửi `listen/stop`. Mode `manual`/`realtime` vẫn theo control/lifecycle
riêng và không bị auto detector chốt lượt.

Từ M2.6, thiết bị có thể tiếp tục gửi uplink khi server đang phát TTS chỉ khi hello đã
công bố `features.aec=true` và mode hiện hành là `realtime`. Với `auto`, `manual`, thiếu
mode hoặc không có AEC, server bỏ frame hợp lệ trong trạng thái speaking. Khi server xác
nhận người dùng chen lời bằng VAD riêng, server purge generation TTS cũ, hủy/chờ pipeline
cũ và gửi đúng một `{"type":"tts","state":"stop"}` thuộc generation mới trước khi thu
turn mới; một lượng pre-roll bounded được giữ để không mất đầu câu. Firmware phải dừng
playback khi nhận `tts/stop`, tiếp tục uplink realtime và không giả định frame TTS cũ còn
hợp lệ sau stop. `listen/start` explicit vẫn là control path tương thích để chủ động thay
turn đang chạy.

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

### Binary frame (Quyết định Veetee - M1.5)

Wire format theo `Protocol-Version` đã thương lượng. Mọi số nguyên đa byte dùng
**network byte order (big-endian)**:

- **Version 1**: raw Opus payload — toàn bộ binary frame là payload, không header.
- **Version 2**: header 16 byte:
  ```text
  | version u16 | type u16 | reserved u32 | timestamp_ms u32 | payload_size u32 | payload |
  ```
  `version=2`, `type=0` (OPUS), `reserved=0`; `payload_size` phải khớp đúng payload còn
  lại (frame dài đúng `16 + payload_size`); `timestamp_ms` là epoch milliseconds.
- **Version 3**: header 4 byte:
  ```text
  | type u8 | reserved u8 | payload_size u16 | payload |
  ```
  `type=0` (OPUS), `reserved=0`; frame dài đúng `4 + payload_size`; không có timestamp.

Server từ chối frame malformed/truncated/oversized bằng error envelope an toàn và close
code tương ứng (1002 cho malformed/mismatch, 1009 cho quá kích thước). Golden vector hợp
lệ và malformed nằm tại `../veetee-server/contracts/device/audio_v{1,2,3}_golden.json` và
`audio_malformed_golden.json`.

Server giới hạn queue audio uplink/downlink theo items, bytes và thời lượng
(`VEETEE_AUDIO_MAX_QUEUE_*`): uplink dùng `drop_oldest`, downlink dùng `fail_session`
(đóng 1009 khi client chậm). `abort`, barge-in và turn mới tăng generation, purge mọi
control/audio cũ đang chờ; thiết bị phải gửi `abort` để hủy luồng cũ thay vì chỉ ngừng đọc.
Từ M2.7, server có native libopus cho E2E và vẫn giữ fake deterministic cho test. Uplink
giải mã thành PCM 16 kHz mono s16le, 960 sample/1920 byte; downlink mã hóa từ PCM 24 kHz
mono s16le, 1440 sample/2880 byte. Packet Opus 60 ms tối đa 3825 byte; lifecycle codec là
theo turn và phải được đóng khi cleanup.

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

## OTA/config discovery (M1.4 - Quyết định Veetee)

Endpoint thiết bị gọi để discover server time, WebSocket URL/token và trạng thái
firmware. Golden vector chung nằm tại
`../veetee-server/contracts/device/ota_check_request.json` và
`ota_check_response.json`.

### Endpoint

```text
GET  /api/v1/devices/ota/check   (fallback khi không có body system info)
POST /api/v1/devices/ota/check   (mặc định; body là system info JSON)
```

### Request headers (bắt buộc)

- `Device-Id`: non-empty, <= 128 ký tự.
- `Client-Id`: non-empty, <= 128 ký tự.

Tùy chọn có giới hạn: `User-Agent` <= 256, `Accept-Language` <= 128. POST có body phải
kèm `Content-Type: application/json` (firmware baseline luôn gửi header này). Body tối đa
16 KiB; JSON phải là object, depth tối đa 8. Body rỗng được chấp nhận.

### Response 200

```json
{
  "server_time": {
    "timestamp": 1724150400000,
    "timezone_offset": 420
  },
  "websocket": {
    "url": "ws://<host>:<port>/api/v1/devices/ws",
    "token": "<gateway-token>",
    "version": 1
  },
  "firmware": {
    "version": "",
    "url": ""
  }
}
```

- `server_time.timestamp` là **epoch milliseconds** (baseline firmware chia 1000 khi set
  clock); `timezone_offset` là phút so với UTC và được firmware cộng vào timestamp.
- `websocket.url` luôn là ws/wss, không userinfo/query/fragment.
- `websocket.token` là gateway token dùng cho handshake WebSocket; chỉ xuất hiện ở
  response này.
- `firmware` trả no-update (`version`, `url` rỗng) ở M1.4: firmware chỉ trigger update khi
  cả hai field là string và version mới lớn hơn version hiện tại; version rỗng không bao
  giờ lớn hơn.
- M1.4 không trả `mqtt` hay `activation`.

Error: `400 veetee_invalid_input` (header/JSON/depth/bounds), `413
veetee_payload_too_large`, `415 veetee_invalid_input` (Content-Type sai). Envelope:
`{"code": "veetee_*", "message": "...", "request_id": "..."}`.

## Yêu cầu contract nếu áp dụng cho Veetee

- Giữ wire behavior cần cho firmware tham khảo nhưng dùng endpoint và namespace Veetee;
  cấm đưa tên/path/metadata upstream vào public contract sản phẩm. URL WebSocket Veetee
  được phân phối qua OTA/config discovery.
- Các path `/api/v1/devices/ws`, `/api/v1/devices/ota/check` và
  `/api/v1/devices/ota/artifacts/{artifact_id}` đã được khóa ở M0.2/Cổng 0;
  `/api/v1/devices/ota/check` đã có responder ở M1.4. Schema versioned và golden vector
  dùng chung nằm tại `../veetee-server/contracts/device/`. Artifact download sẽ triển
  khai ở M5.
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
