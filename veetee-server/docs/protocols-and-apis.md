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
6. `mcp` và unsupported frames:
   - Trả safe typed error envelope `veetee_invalid_input`. Tích hợp MCP pipeline hoãn lại M3.

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

### Mở rộng vòng đời M5

Khi `VEETEE_PERSISTENCE_ENABLED=true`, responder M5 thay shared fleet token bằng vòng
đời thiết bị có persistence:

- Admin provision out-of-band `Device-Id`, `Client-Id` tùy chọn và Ed25519 public key raw
  32 byte tại `/api/v1/control/devices/provision`; server không nhận/lưu private key.
  Discovery đầu của identity đã provision chỉ trả `activation.nonce` opaque và TTL, không
  trả code/token. Thiết bị ký đúng bytes
  `veetee-activation-v1\n<device_id>\n<client_id>\n<nonce>\n`, gửi chữ ký raw 64 byte hex
  trong `Activation-Proof` cùng `Activation-Nonce`. Nonce một lần, có TTL/attempt limit;
  replay bị từ chối. Chỉ response proof hợp lệ mới trả code 6 chữ số và bootstrap token
  một lần để thiết bị hiển thị code vật lý. User control API không có endpoint đọc code.
- Identity chưa provision nhận `activation.status=pending`, không có nonce/code/token và
  không được tự tạo production enrollment. Compatibility cũ chỉ bật rõ bằng
  `VEETEE_ALLOW_INSECURE_ACTIVATION=true` khi environment là `local` hoặc `test`; mặc định
  false và production readiness/config cấm bật.
- Bootstrap/recovery token không phải credential WebSocket và bị từ chối tại WebSocket,
  OTA report và artifact download. Sau bind/recovery, token này chỉ được chấp nhận đúng
  một lần tại discovery để cấp credential `veetee-device-ws`; insert token mới và revoke
  token một lần diễn ra trong cùng transaction, nên replay thất bại.
- Discovery của thiết bị đã bind bắt buộc gửi `Authorization: Bearer <active-token>` khớp
  chính xác `Device-Id`/`Client-Id`. Server lock/load device và xác thực credential trước
  khi nhận board/chip/partition/version quan sát hoặc rotate token; không fallback shared
  token khi persistence bật và credential cũ chỉ bị revoke sau khi token mới được ghi.
  Credential HMAC-SHA256 TTL ngắn có `iss=veetee-server`,
  `aud=veetee-device-ws`, `device_id`, `client_id`, `jti`, `iat`, `exp`. Lần discovery
  sau revoke credential active trước của cùng cặp device/client. WebSocket kiểm chữ ký,
  claims, JTI active, trạng thái bind và không fallback shared token khi persistence bật.
- Shared `VEETEE_DEVICE_GATEWAY_TOKEN` chỉ còn là compatibility mode khi persistence tắt.
  Unbind revoke toàn bộ credential của thiết bị.
- Firmware baseline hiện tại chưa gửi Authorization ở bound rediscovery; firmware/client
  production phải được thích nghi contract này trước khi bật persistence M5. Simulator
  Veetee có thể gửi token; không patch repo tham khảo.
- OTA chỉ trả release SemVer 2 cao nhất, đúng tuyệt đối board/chip/partition và
  channel, thuộc rollout `active` theo cohort SHA-256 deterministic. Rollout `paused` hoặc
  `killed`, `auto_update=false` hoặc thiếu partition đều trả no-update chính xác.

Artifact URL mang token HMAC TTL ngắn ràng buộc đúng device/artifact. Download chỉ cho
artifact vẫn thuộc release/rollout đủ điều kiện và SHA-256 của toàn file trên disk còn
khớp trước khi trả byte đầu, hỗ trợ đúng một byte range (`start-end`,
`start-` hoặc suffix `-length`); malformed/multi-range trả `416`. Upload control plane
stream body `application/octet-stream`, bắt buộc SHA-256, detached Ed25519 signature và
target metadata qua header; file được fsync + publish atomically và artifact/release/report
là append-only. Device gửi report tại `POST /api/v1/devices/ota/report` bằng credential
WS cùng `Device-Id`/`Client-Id`; download/install/boot/rollback bắt buộc `release_id`, phải
khớp release published đã được offer và đúng progression. `event_id` retry cùng payload
là idempotent, payload khác trả `409`; chỉ boot success của release đó cập nhật version
authoritative. Discovery version chỉ là observed telemetry. Failure gate cấu hình được tự
pause rollout active khi đủ sample/minimum; insert/count/pause được serialize bằng rollout
row/advisory lock. Report có quota persistent theo device/giờ, terminal uniqueness và
dedupe window cho check/in-progress. Retention cleanup chỉ chạy khi operator gọi explicit,
không có bulk delete tự động. Summary nhóm theo board/version/cohort.

M4 row chưa có `Client-Id` được migrate thành `recovery_required`, giữ nguyên owner. Chỉ
owner đó hoặc admin được gọi recovery để gắn Client-Id đầu tiên. Bind/unbind bắt buộc
`Idempotency-Key`; cùng actor/action/payload replay kết quả ổn định, payload khác trả conflict.
Release lưu provenance và rollback target. Admin rollback tạo một rollout target chuyên biệt
cùng authorization persistent gắn source rollout/release, exact target release và scope
rollout/cohort/device. Mọi eligibility trên rollout rollback đều bắt buộc authorization khớp,
kể cả khi version hiện tại thấp hơn target. Scope device/cohort giữ source rollout active cho
thiết bị ngoài scope; chỉ scope rollout mới kill source toàn cục. Authorization còn hiệu lực
mới cho phép offer/download/report/boot rollback, và boot success cập nhật current version.
Artifact metadata trả SHA-256, Ed25519 signature, algorithm, key id và size. Parser firmware
baseline bỏ qua field firmware thừa, nhưng client production vẫn phải verify digest và
signature trước install.

Mọi OTA fleet API dưới `/api/v1/control/ota` chỉ dành cho role `admin`; owner không tự
động trở thành admin. Identity cấu hình bởi `VEETEE_BOOTSTRAP_ADMIN_EMAIL` được tạo hoặc
promote thành admin một cách deterministic và có audit. Recovery trả riêng một
`recovery_token` một lần qua response control plane owner/admin, không bao giờ trả WS token
cho browser. Artifact/release bắt buộc provenance bounded. URL tải artifact chỉ được tạo từ
`VEETEE_OTA_PUBLIC_BASE_URL`, bắt buộc HTTPS ngoài local/test, không dùng Host header khi
persistence bật. Discovery persistence production cũng bắt buộc public WebSocket `wss://`;
`http://`/`ws://` chỉ được phép khi environment được đặt rõ là `local` hoặc `test`.

Identity chưa provision không có activation code. Public key phải được provision qua kênh
quản trị tin cậy trước first contact; private key chỉ tồn tại trên thiết bị. Code sau proof
vẫn phải được người dùng đọc từ kênh vật lý để hoàn tất binding.

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
| `Authorization` | Bắt buộc cho bound rediscovery; Bearer active per-device token |
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
headers cho phép gồm `Authorization, Device-Id, Client-Id, User-Agent, Accept-Language, Content-Type,
X-Veetee-Request-Id`. Không dùng `Access-Control-Allow-Credentials` (endpoint device
không dùng cookie/credential trình duyệt).

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

## MCP device protocol

Server đóng vai MCP client đối với ESP32. Flow:

```text
device hello features.mcp=true
  -> server initialize
  -> device capabilities/serverInfo
  -> server tools/list (có pagination)
  -> LLM/backend chọn tool
  -> server tools/call
  -> device result/error
```

MCP payload là JSON-RPC 2.0 bọc trong message `type=mcp`. Tool invocation cần được ràng
buộc vào đúng session/device và authorization policy. Không để model tự động gọi
user-only tool như reboot/upgrade.

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

## Source đối chiếu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/http_server.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/api/`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/handle/`
- `../references/xiaozhi-esp32-server/main/manager-api/src/main/java/xiaozhi/modules/`
- `../references/xiaozhi-esp32-server/docs/mqtt-gateway-integration.md`
- `../references/xiaozhi-esp32-server/docs/mcp-endpoint-integration.md`
