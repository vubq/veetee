# Giao thức và API

## Device WebSocket Veetee (Quyết định Veetee - M1.3)

Endpoint chính thức của Veetee Server:

```text
ws://<host>:<port>/api/v1/devices/ws
```

### Handshake HTTP Headers

- `Authorization: Bearer <token>` (Bắt buộc; so sánh constant-time với `VEETEE_DEVICE_GATEWAY_TOKEN`).
- `Protocol-Version: 1 | 2 | 3` (Bắt buộc; server hỗ trợ 1, 2 và 3 từ M1.5).
- `Device-Id: <string>` (Bắt buộc; non-empty, tối đa `VEETEE_ID_MAX_LENGTH=128`).
- `Client-Id: <string>` (Bắt buộc; non-empty, tối đa `VEETEE_ID_MAX_LENGTH=128`).

Protocol version xác định wire format cho toàn bộ binary audio frame của session; version
2 và 3 thêm header frame nhị phân, version 1 là raw payload Opus (xem mục Binary Frames).

### Quy định Frame & Payload Limits

- `VEETEE_HELLO_TIMEOUT_SECONDS`: 10.0s (bắt buộc gửi frame text `hello` trong 10s đầu).
- `VEETEE_IDLE_TIMEOUT_SECONDS`: 60.0s (transport activity timeout).
- `VEETEE_CONVERSATION_IDLE_TIMEOUT_SECONDS`: 180.0s kể từ hoạt động hội thoại cuối cùng;
  reset khi có speech mới và sau khi gửi xong `tts/stop`. Không giới hạn tổng thời lượng
  hội thoại. `VEETEE_CONVERSATION_PLAYBACK_DRAIN_SECONDS` mặc định 3.0s bù audio còn trong
  buffer phát của thiết bị.
- `VEETEE_PING_INTERVAL_SECONDS`: 20.0s.
- `VEETEE_PONG_TIMEOUT_SECONDS`: 10.0s.
- `VEETEE_JSON_MAX_BYTES`: 16384 bytes (16 KiB).
- `VEETEE_JSON_MAX_DEPTH`: 8.
- `VEETEE_BINARY_MAX_BYTES`: 65536 bytes (64 KiB) — áp dụng cho tổng frame binary
  (header + payload) và cho payload khai báo trong header v2/v3.
- `VEETEE_CLEANUP_TIMEOUT_SECONDS`: 5.0s.
- `VEETEE_AUDIO_MAX_QUEUE_ITEMS`: 100 — giới hạn số item mỗi queue audio.
- `VEETEE_AUDIO_MAX_QUEUE_BYTES`: 1048576 (1 MiB) — giới hạn tổng payload mỗi queue audio.
- `VEETEE_AUDIO_MAX_QUEUE_DURATION_MS`: 10000.0 — giới hạn tổng thời lượng audio mỗi queue;
  phải >= 60ms (một audio frame) theo validator config.
- `VEETEE_AUDIO_PACING_MAX_DRIFT_MS`: 100.0 — drift tối đa của downlink pacer trước khi reset
  anchor; phải nhỏ hơn `audio_max_queue_duration_ms` theo validator config.
- `VEETEE_BARGE_IN_PRE_ROLL_FRAMES`: 5 — số frame uplink 60 ms tối đa giữ lại cho
  barge-in; phải nằm trong khoảng 1..20.

### Error Envelope Format

Mọi safe error envelope trả về từ server có cấu trúc:

```json
{
  "code": "veetee_invalid_input",
  "message": "Chi tiết lỗi an toàn (không lộ secret hay raw payload)",
  "session_id": "uuid-string-hoặc-null"
}
```

Các mã lỗi thuộc M0 taxonomy: `veetee_invalid_input`, `veetee_auth_failed`, `veetee_timeout`, `veetee_internal`.

### WebSocket Close Codes

- `1000`: Goodbye / normal closure.
- `1001`: Idle timeout.
- `1002`: Protocol confusion (binary frame trước hello; binary audio frame malformed,
  truncation hoặc header version mismatch với version đã thương lượng).
- `1008`: Header/Auth failure, Hello timeout, schema violation, duplicate hello, session mismatch.
- `1009`: Message too big (JSON > 16 KiB, binary > 64 KiB, hoặc payload khai báo trong
  header v2/v3 vượt `VEETEE_BINARY_MAX_BYTES`).
- `1011`: Internal server error.
- `1012`: Service restart / graceful shutdown.

### Semantic JSON Control Frames (M1.3)

1. `hello`:
   - Device -> Server: `type: "hello"`, `version: 1`, `transport: "websocket"`, `audio_params: {format: "opus", sample_rate: 16000, channels: 1, frame_duration: 60}`. `features` là mapping boolean tùy chọn (tối đa 16 keys, key max 64 chars). Firmware có display được phép gửi `features.glyph_push` và `text_font: {bundle, charset, size, bpp}`; string tối đa 128 ký tự, `size` trong 1..256 và `bpp` thuộc 1/2/4/8, mọi field thừa bị từ chối.
   - Server -> Device: `type: "hello"`, `transport: "websocket"`, `session_id: "<opaque UUID>"`, `audio_params: {format: "opus", sample_rate: 24000, channels: 1, frame_duration: 60}`.
2. `ping` / `pong`:
   - Device -> Server `ping` -> Server trả `{"type": "pong", "session_id": "..."}`.
   - Device -> Server `pong` -> Server cập nhật heartbeat.
3. `goodbye`:
   - Device -> Server `goodbye` -> Server trả `goodbye` và close websocket code 1000.
4. `abort`:
   - Device -> Server `abort` -> Idempotent abort active turn/generation. Firmware được
     phép thêm `reason="wake_word_detected"`; reason khác bị từ chối.
5. `listen`:
   - Device -> Server `listen`: `state` (`start`, `stop`, `detect`), `mode` (`auto`, `manual`, `realtime`). `detect` được phép mang wake phrase trong `text` dài 1..128 ký tự; `text` bị từ chối ở `start`/`stop`. Quản lý state machine `DeviceSession`.
    - Binary audio hợp lệ được enqueue từ `listen/start` tới `listen/stop`. Ngoài
      `LISTENING`, frame hợp lệ mặc định bị drop; ngoại lệ M2.6 là khi session đang
      `SPEAKING`, hello đã công bố `features.aec=true` và mode hiện hành là `realtime`.
      Frame malformed/oversized vẫn đóng `1002`/`1009` trước khi áp state gate.
    - Với mode `auto`, server dùng decoder/VAD turn-scoped riêng để phát hiện
      `SPEECH_END` trên stream liên tục và tự bắt đầu pipeline; firmware không bắt buộc gửi
      `listen/stop`. `manual` và `realtime` không dùng auto endpoint detector này.
    - Với mode `auto`, silence/noise uplink không reset conversation timeout; sau khi hết
      thời gian không có hoạt động hội thoại, server đóng WebSocket bình thường để firmware
      về `idle` và chờ WakeNet.
   - Từ M1.6, `listen/stop` chạy fake pipeline deterministic và server phát theo thứ tự
     `stt` -> `tts/start` -> `tts/sentence_start` -> binary audio* -> `tts/stop`.
6. `mcp` (M6.7) và unsupported frames:
   - `mcp` chỉ hợp lệ khi outer envelope có đúng `type`, `session_id` của live connection
     và `payload` JSON-RPC 2.0. Server gửi request `initialize`, `tools/list` và
     `tools/call`; device trả response có cùng `id` với đúng một trong `result`/`error`.
   - Response lạ, trùng, trễ hoặc thuộc session cũ bị ignore; malformed/unsolicited
     request trả safe `veetee_invalid_input` không echo payload và không đóng session.
   - Message type khác không hỗ trợ tiếp tục trả safe `veetee_invalid_input`.

### Full-duplex và barge-in (M2.6)

- Server chỉ chạy detector chen lời trong khi `SPEAKING` nếu đồng thời có
  `hello.features.aec=true` và `listen.mode="realtime"`. Mode `auto`, `manual`, mode chưa
  được thiết lập hoặc thiết bị không công bố AEC tiếp tục drop uplink khi phát TTS.
- Detector dùng decoder/VAD stream riêng với turn cũ và giữ bounded pre-roll theo
  `VEETEE_BARGE_IN_PRE_ROLL_FRAMES`; trigger frame chỉ xuất hiện một lần trong pre-roll.
- Khi VAD xác nhận `SPEECH_START`, server tăng queue generation đúng một lần, purge output
  cũ, reset/đánh thức pacer, cancel và chờ cleanup pipeline/provider cũ, rồi phát đúng một
  `tts/stop` thuộc generation mới. Server mở turn `LISTENING` mới và retag pre-roll vào
  ingress generation mới.
- Expected-turn guard làm trigger đến trễ sau abort/completion hoặc trigger đồng thời trở
  thành no-op; frame audio đã dequeue nhưng còn chờ pacer được kiểm tra generation lại và
  không được gửi sau barge-in.
- `listen/start` explicit vẫn tương thích và tiếp tục thay thế turn đang xử lý/phát. Đây là
  control path chủ động, không phụ thuộc điều kiện AEC/realtime của detector tự động.

### Binary Frames

- Binary trước hello: protocol error (close 1002).
- Binary sau hello: parse theo `Protocol-Version` đã thương lượng trong handshake.

#### Wire format (Quyết định Veetee - M1.5)

Mọi số nguyên đa byte dùng **network byte order (big-endian)**.

**Version 1 — raw Opus:**

```text
| Opus payload (len = độ dài frame) |
```

Không có header. Toàn bộ binary frame là payload Opus; timestamp không có trên wire.
`VEETEE_BINARY_MAX_BYTES` áp dụng cho toàn bộ frame.

**Version 2 — header 16 byte:**

```text
| version (u16) | type (u16) | reserved (u32) | timestamp_ms (u32) | payload_size (u32) | payload |
```

- `version` phải là `2`; `type` phải là `0` (OPUS); `reserved` phải là `0`.
- `timestamp_ms` là epoch milliseconds của frame (giá trị 0 được encode khi không có).
- `payload_size` phải bằng đúng số byte payload còn lại; frame dài đúng
  `16 + payload_size`. Không cho phép padding.

**Version 3 — header 4 byte:**

```text
| type (u8) | reserved (u8) | payload_size (u16) | payload |
```

- `type` phải là `0` (OPUS); `reserved` phải là `0`.
- `payload_size` là u16; frame dài đúng `4 + payload_size`. Không có timestamp trên wire.

Mọi violation cấu trúc (truncated header/payload, type hoặc reserved sai, payload size
không khớp, version mismatch với negotiated version) trả error envelope
`veetee_invalid_input` và đóng close 1002. Frame vượt `VEETEE_BINARY_MAX_BYTES` (tổng độ
dài hoặc `payload_size` khai báo) trả `veetee_invalid_input` và đóng close 1009.

#### Native Opus (Quyết định Veetee - M2.7)

`VEETEE_AUDIO_CODEC=fake|native` chọn codec theo turn; mặc định `fake` giữ unit/integration
test deterministic. Mode `native` dùng `libopus` qua stdlib `ctypes`, tạo encoder/decoder
stateful riêng cho từng turn và barge-in detector, rồi đóng idempotent khi turn kết thúc.
Readiness trả `503` với reason `native_opus_not_ready` nếu shared library không load được.

- Uplink: Opus -> PCM 16 kHz, mono, s16le, 60 ms, đúng 960 sample/1920 byte.
- Downlink: PCM 24 kHz, mono, s16le, 60 ms, đúng 1440 sample/2880 byte -> Opus.
- Packet Opus 60 ms bị giới hạn tối đa `3 * 1275 = 3825` byte theo ba frame 20 ms.
- Payload rỗng, malformed, oversized hoặc PCM sai alignment/size bị từ chối bằng lỗi codec
  typed; codec đã đóng không được tái sử dụng.
- Resampler khác sample rate vẫn là deferred boundary; passthrough chỉ được phép khi hai
  PCM format giống nhau sau validation.

#### Queue policy (M1.5)

Mỗi session có hai queue giới hạn đồng thời theo 3 chiều (items, bytes, duration_ms):

- `ingress_queue` (uplink từ device): policy `drop_oldest` — khi đầy, item cũ nhất bị bỏ
  để giữ session sống, kết hợp `VEETEE_AUDIO_MAX_*`.
- `egress_queue` (downlink tới device): policy `fail_session` — khi đầy do client chậm,
  raise `SlowClientQueueOverflowError` và đóng session (1009).
- Mỗi item mang `generation`; `abort`, barge-in và ranh giới `listen/start` của turn mới
  tăng generation, purge toàn bộ control/audio cũ đang chờ và chặn output stale chảy vào
  lượt mới.
- `get()`/`close()` là cancellation-aware; `close()` đánh thức mọi waiter đang chờ.

Golden vector cho v1/v2/v3 hợp lệ và malformed/truncated/oversized nằm tại
`../contracts/device/audio_v{1,2,3}_golden.json` và `audio_malformed_golden.json`.

---

## OTA/config discovery Veetee (Quyết định Veetee - M1.4)

Endpoint thiết bị gọi để nhận server time, WebSocket URL/token và trạng thái firmware.

```text
GET  /api/v1/devices/ota/check   (fallback khi không có body system info)
POST /api/v1/devices/ota/check   (firmware baseline gửi system info JSON)
OPTIONS /api/v1/devices/ota/check (CORS preflight, credentials-free)
```

### Request

Header bắt buộc (có phân biệt hoa thường, so khớp không phân biệt case):

| Header | Yêu cầu |
| --- | --- |
| `Device-Id` | non-empty, <= `VEETEE_ID_MAX_LENGTH=128` |
| `Client-Id` | non-empty, <= `VEETEE_ID_MAX_LENGTH=128` |

Header tùy chọn có giới hạn:

| Header | Giới hạn |
| --- | --- |
| `User-Agent` | <= 256 chars |
| `Accept-Language` | <= 128 chars |
| `Content-Type` | POST có body phải là `application/json` (kèm charset chấp nhận) |

Body POST:

- Giới hạn kích thước trước khi parse: `VEETEE_JSON_MAX_BYTES=16384` (413 nếu vượt).
- JSON phải là object; depth tối đa `VEETEE_JSON_MAX_DEPTH=8`.
- Ràng buộc cấu trúc: tối đa 32 key mỗi object, key <= 64 chars, string value <= 256
  chars, array <= 32 phần tử; value phải là string/number/boolean/null/object/list một cấp.
- Body rỗng được chấp nhận (tương đương GET); body không phải JSON bị từ chối 400/415.

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

- `server_time.timestamp`: **epoch milliseconds** (baseline firmware chia 1000 để ra
  giây khi set clock; gửi giây sẽ làm clock thiết bị sai về ~1970).
- `server_time.timezone_offset`: offset phút so với UTC của server host.
- `websocket.url`: `VEETEE_DEVICE_WEBSOCKET_PUBLIC_URL` nếu cấu hình, ngược lại suy từ
  `VEETEE_HOST:VEETEE_PORT`. Bắt buộc `ws`/`wss`, không userinfo, không query/fragment.
- `websocket.token`: `VEETEE_DEVICE_GATEWAY_TOKEN`; chỉ xuất hiện ở response OTA này,
  không ghi vào log. Token rỗng khi chưa cấu hình.
- `firmware`: luôn là no-update ở M1.4 (`version` và `url` rỗng). Firmware baseline chỉ
  nâng cấp khi cả `version` lẫn `url` là string và version mới lớn hơn version hiện tại;
  version rỗng parse thành 0 component nên không bao giờ trigger update.
- Không có object `mqtt`, `activation` ở M1.4.

### Lỗi

| Status | Code | Ý nghĩa |
| --- | --- | --- |
| 400 | `veetee_invalid_input` | Header thiếu/sai, JSON sai cú pháp/không phải object, vượt depth/bounds |
| 413 | `veetee_payload_too_large` | Body vượt `VEETEE_JSON_MAX_BYTES` |
| 415 | `veetee_invalid_input` | Body có nhưng `Content-Type` không phải `application/json` |

Error envelope: `{"code": "veetee_*", "message": "...", "request_id": "..."}`. Mọi
response OTA (kể cả lỗi) có header `X-Veetee-Request-Id` và `Access-Control-Allow-Origin: *`.

### CORS

`OPTIONS` trả 204 với `Access-Control-Allow-Origin: *`, methods `GET, POST, OPTIONS`,
headers cho phép gồm `Device-Id, Client-Id, User-Agent, Accept-Language, Content-Type,
X-Veetee-Request-Id`. Không dùng `Access-Control-Allow-Credentials` (endpoint device
không dùng cookie/credential trình duyệt).

### M5 activation, binding và OTA local cơ bản

Khi PostgreSQL persistence được bật, `POST /api/v1/devices/ota/check` cho thiết bị chưa
bind trả thêm `activation` gồm code sáu chữ số, challenge không rỗng, thời gian sống còn
lại theo millisecond và `message` đúng dạng `<console URL>\n<code>`. Code/challenge giữ
nguyên trong TTL mặc định 10 phút. `POST /api/v1/devices/ota/check/activate` chỉ nhận body
Version1 chính xác `{}`: trả `202 {"activated": false}` trước bind và
`200 {"activated": true}` sau bind. `Device-Id` đã bind với `Client-Id` khác bị từ chối.

Control plane bearer-authenticated cung cấp:

- `POST /api/v1/control/devices/bind` với `agent_id` và `code`; agent phải thuộc tenant.
  Brute-force được giới hạn bằng quota theo user/cửa sổ thời gian. Bind thành công tạo
  receipt chỉ chứa hash code, sống trong TTL ngắn để retry cùng user/agent/code idempotent;
  receipt hết hạn được dọn và code sáu chữ số có thể tái sử dụng an toàn.
- `GET /api/v1/control/devices`; `online` lấy từ WebSocket session registry hiện hành.
- `DELETE /api/v1/control/devices/{id}`; unbind tenant-scoped và ghi audit.
- `POST /api/v1/control/ota/artifacts`; body raw `application/octet-stream`, stream có
  giới hạn kích thước và SHA-256, tên lưu do server tạo và artifact bất biến.
- `POST /api/v1/control/ota/releases` và
  `POST /api/v1/control/ota/releases/{id}/publish`; release khóa version/board/chip/
  partition/force và chỉ tham chiếu artifact cùng owner.

System-info firmware hiện tại được đọc từ `application.version`, `board.type` (fallback
`board.name`), `ota.label` và `chip_model_name`; metadata đã bind giữ đúng các giá trị này.
Thiết bị bound chỉ nhận release cùng owner có board/chip/partition khớp chính xác và
version cao nhất lớn hơn version hiện tại; release `force=true` vẫn eligible và được
serialize thành JSON number `force: 1` vì firmware hiện tại kiểm kiểu number. URL artifact
chỉ sinh từ `VEETEE_OTA_PUBLIC_BASE_URL` đã validate, không dùng request `Host`. Download dùng
`GET /api/v1/devices/ota/artifacts/{artifact_id}`, chỉ resolve storage name do server tạo
trong artifact root, hash SHA-256 lại theo chunk trước khi stream và không nạp toàn bộ vào
RAM. Phạm vi rút gọn này chưa có
signature, channel/cohort, rollout percentage hay firmware report.

---

## Endpoint mặc định quan sát từ firmware tham khảo (Upstream reference)

```text
ws://<host>:8000/xiaozhi/v1/
```

Handshake header gồm `Authorization`, `Protocol-Version`, `Device-Id`, `Client-Id`.
Text frame là JSON control; binary frame là Opus hoặc binary envelope theo protocol
version. Đặc tả message chi tiết nằm ở `../../veetee-firmware/docs/device-server-protocol.md`.

Server nhận `hello`, `listen`, `abort`, `iot`, `mcp`, `server`, `ping`. Server gửi
`hello`, `stt`, `llm`, `tts`, `mcp`, `system`, `alert` và binary audio. Hai đầu phải
kiểm tra `session_id` và feature negotiation.

## MQTT gateway

Python server có thể nhận kết nối bridge qua WebSocket path có `?from=mqtt_gateway`.
Gateway chuyển MQTT control và UDP audio thành hợp đồng nội bộ cho `ConnectionHandler`.
Đây là implementation detail upstream; Veetee cần quyết định gateway là process riêng,
protocol nội bộ nào và trust boundary ở đâu.

## Python HTTP service

Cổng mặc định quan sát là `8003`.

| Method | Path | Khi nào có | Vai trò |
| --- | --- | --- | --- |
| GET/POST/OPTIONS | `/xiaozhi/ota/` | Local mode | Trả WebSocket/OTA config cho device |
| GET/OPTIONS | `/xiaozhi/ota/download/{filename}` | Local mode | Chỉ download file trong `data/bin` |
| GET/POST/OPTIONS | `/mcp/vision/explain` | Luôn đăng ký | Nhận ảnh/câu hỏi cho vision model |

Khi `read_config_from_api=true`, OTA route local không được đăng ký; control plane đảm
nhận OTA/config. Download handler phải chống path traversal và giới hạn file/content.
Vision endpoint cần giới hạn upload, MIME, timeout, token và chống SSRF nếu gọi URL ngoài.

## MCP device protocol (Quyết định Veetee - M6.7)

Server đóng vai MCP client đối với ESP32. Device phải công bố `hello.features.mcp=true`.
Outer envelope hai chiều:

```json
{
  "type": "mcp",
  "session_id": "<live-session-uuid>",
  "payload": {"jsonrpc": "2.0", "id": "vtmcp-...", "method": "tools/list", "params": {}}
}
```

Flow:

```text
device hello features.mcp=true
  -> server initialize
  -> device capabilities/serverInfo
  -> server tools/list (tối đa 10 trang/100 tool, cursor không được lặp)
  -> LLM/backend chọn tool
  -> server tools/call
  -> device result/error
```

Correlation ID string tối đa 128 ký tự; error message tối đa 512 ký tự. Pending request
được giới hạn theo session, timeout mặc định 10 giây; timeout, cancellation, disconnect,
duplicate và stale response đều cleanup deterministic. Mọi sender JSON/audio/MCP của một
WebSocket dùng chung send lock để không concurrent-send.
Golden vector chung nằm tại `../contracts/device/mcp_golden.json`.

Control plane owner-scoped cung cấp:

- `POST /api/v1/control/devices/{device_pk}/mcp/tools/list`: discovery trên đúng live
  session; nếu có nhiều session phải truyền `session_id` explicit; không cần confirmation.
- `POST /api/v1/control/devices/{device_pk}/mcp/tools/{tool_name}/prepare-call`: nhận
  `session_id` tùy chọn và `arguments`; trả token plaintext đúng một lần, TTL mặc định 60s.
- `POST /api/v1/control/devices/{device_pk}/mcp/tools/{tool_name}/call`: nhận token; token
  được consume trước validation/execution nên timeout, lỗi, mismatch và replay đều không
  thể dùng lại.

Server chỉ lưu SHA-256 token trong bounded memory và bind confirmation vào exact owner,
device primary key/device ID/client ID/agent ID/live session/tool/arguments bằng canonical
digest. Execute tái xác minh toàn bộ binding và capability `features.mcp=true`; offline,
unbound hoặc mismatch fail closed. Audit không lưu token, arguments hay result. Address
book/device calling và process restart qua device message không thuộc phạm vi đã duyệt.

## Manager API

Spring Boot context quan sát là `/xiaozhi`, thường ở cổng `8002`. OpenAPI/Knife4j có
thể có tại `/xiaozhi/doc.html`. API đầy đủ rất lớn; các nhóm quan trọng:

| Nhóm | Base path ví dụ | Vai trò |
| --- | --- | --- |
| Runtime config | `/config` | Server base, agent models, correction words |
| Authentication | `/user` | Login, register, info, password, public config |
| Device/agent | Theo controller `device`, `agent` | Bind và cấu hình trợ lý |
| Model/provider | `/models`, `/models/provider` | CRUD provider/model/voice |
| OTA | Controller trong module OTA | Firmware, version và rollout |
| Voice | `/ttsVoice`, `/voiceClone`, `/voiceResource` | Timbre và clone voice |
| Knowledge | `/datasets` | Dataset/document cho RAG |
| Administration | `/admin/...` | User, role, parameter, dictionary, server action |

Ba API nội bộ mà Python runtime phụ thuộc trực tiếp:

```text
POST /xiaozhi/config/server-base
POST /xiaozhi/config/agent-models
POST /xiaozhi/config/correct-words
```

Cần đọc `ConfigController.java` và DTO tương ứng để lấy body/response chính xác tại
commit đang pin. Không nên coi bảng tổng hợp này là OpenAPI contract.

## API design Veetee (đề xuất M0.2)

- Không dùng namespace, path, package hoặc response metadata chứa tên upstream. Firmware
  tham khảo phải nhận endpoint Veetee qua OTA/config discovery thay vì server giữ URL
  upstream. Bảng endpoint và identifier đầy đủ nằm trong
  [chính sách namespace](namespace-policy.md); các path đó vẫn chờ duyệt tại Cổng 0.
- Tách public device API, user API, admin API và service-to-service API.
- Version endpoint/path hoặc media type.
- Dùng OpenAPI sinh từ source và contract test cho client.
- Idempotency cho activation, bind, OTA report và command.
- Pagination/filter/sort nhất quán.
- Error envelope có machine code, message an toàn và correlation ID.
- RBAC tenant-aware; admin action có audit log.
- Rate limit login, activation, upload và AI-cost endpoint.

### Administration API M6.8

- `GET/POST /api/v1/control/admin/users` và
  `PUT /api/v1/control/admin/users/{user_id}`: admin-only, filter/pagination bounded,
  optimistic `version`; suspend user revoke toàn bộ session còn sống trong transaction.
- `POST /api/v1/control/admin/users/{user_id}/reset-token`: trả token plaintext đúng một
  lần; database chỉ lưu SHA-256 và expiry. `POST /api/v1/control/auth/reset-password`
  consume token atomically, chặn replay/expiry và revoke session cũ.
- `GET/PUT /api/v1/control/admin/settings/{key}`: chỉ nhận allowlist setting có type và
  version; không nhận arbitrary key/secret.
- `GET /api/v1/control/admin/audit-logs`: admin-only, filter theo action/resource/actor/
  thời gian và pagination bounded, thứ tự `created_at DESC, id DESC`.
- `GET/PUT /api/v1/control/admin/quotas/{user_id}` và
  `GET /api/v1/control/quotas/me`: policy/usage cho LLM token theo ngày UTC, TTS ký tự theo
  ngày UTC, tool call theo phút UTC cố định và RAG byte theo tháng UTC. Quota mặc định tắt;
  PostgreSQL advisory lock giữ check-and-consume atomic. LLM precheck trước provider và
  ghi usage exact khi provider trả metadata, nên một request đang chạy có thể vượt limit
  tối đa đúng usage của request đó; lượt tiếp theo bị chặn.

## Source đối chiếu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/http_server.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/api/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/handle/`
- `../references/xiaozhi-esp32-server/main/manager-api/src/main/java/xiaozhi/modules/`
- `../references/xiaozhi-esp32-server/docs/mqtt-gateway-integration.md`
- `../references/xiaozhi-esp32-server/docs/mcp-endpoint-integration.md`
