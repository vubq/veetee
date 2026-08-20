# Ma trận tương thích firmware-server

## Trạng thái tài liệu

Tài liệu này hoàn thành task M0.1 của
[kế hoạch triển khai server](server-implementation-plan.md). Nội dung được chia thành:

- **Hành vi quan sát:** đọc từ firmware tham khảo tại commit
  `d6f6b642977940b862f6f3026c3915df75d388b6`, không phải contract Veetee.
- **Contract Veetee đề xuất:** cách Veetee giữ wire behavior cần thiết nhưng dùng
  namespace/path của Veetee. Các phần ghi `proposed` phải được khóa ở M0.2 và duyệt tại
  Cổng 0 trước khi viết implementation production.
- **Chưa xác minh:** cần golden vector, simulator hoặc test trên thiết bị thật.

Không sửa firmware tham khảo để tạo tài liệu này. Hai repo `references/` vẫn là input
read-only.

## 1. Luồng khởi động và discovery

### Hành vi quan sát

Firmware thực hiện sau khi network connected:

```text
network connected
  -> ActivationTask
  -> POST/GET version check URL
  -> parse activation, server_time, websocket/mqtt, firmware
  -> nếu có firmware mới: download/flash/reboot
  -> nếu có activation code/challenge: retry Activate()
  -> chọn MQTT nếu có mqtt config, nếu không chọn WebSocket nếu có websocket config
  -> idle
```

Bằng chứng chính:

- `main/application.cc:275-355`: tạo `ActivationTask`, kiểm tra asset/firmware, khởi tạo
  protocol sau discovery.
- `main/application.cc:417-504`: retry version check, firmware update, activation code
  và challenge.
- `main/application.cc:506-520`: ưu tiên MQTT nếu response có MQTT, WebSocket nếu không
  có MQTT nhưng có WebSocket.
- `main/ota.cc:46-71`: URL lấy từ setting `ota_url` hoặc build config; request gửi
  `Device-Id`, `Client-Id`, `User-Agent`, `Accept-Language`, `Content-Type` và tùy chọn
  `Activation-Version`, `Serial-Number`.
- `main/ota.cc:77-110`: gửi system info bằng POST nếu có, GET nếu body rỗng; yêu cầu HTTP
  status 200.

### Response tối thiểu được firmware đọc

`main/ota.cc:112-243` parse các object sau, tất cả đều có thể vắng mặt:

| Object | Field quan sát | Consumer | Mức cần cho WebSocket test |
| --- | --- | --- | --- |
| `activation` | `message`, `code`, `challenge`, `timeout_ms` | UI/activation loop | Không bắt buộc nếu device đã bind |
| `websocket` | các key string/number, thực tế gồm URL/token/version | `WebsocketProtocol` settings | Bắt buộc |
| `mqtt` | các key string/number | `MqttProtocol` settings | Không trả khi test WebSocket |
| `server_time` | `timestamp`, `timezone_offset` | system clock | Khuyến nghị |
| `firmware` | `version`, `url`, `force` | OTA selector | Trả không có update ở M1 |

Firmware ghi các key trong `websocket` trực tiếp vào persistent settings. Vì vậy server
  phải kiểm soát allowlist field, size, scheme và URL trước khi phát response; không đưa
  secret mới vào field tùy ý.

### Contract Veetee đề xuất tại M0.1

| Path đề xuất | Producer | Consumer | Trạng thái |
| --- | --- | --- | --- |
| `POST /api/v1/devices/ota/check` | Veetee Server | firmware `Ota` | proposed → **implemented M1.4** |
| `GET /api/v1/devices/ota/artifacts/{artifact_id}` | Veetee Server | firmware OTA downloader | proposed (M5) |
| URL trong `websocket.url` | Veetee Server response | firmware WebSocket client | proposed → **implemented M1.4** |
| URL WebSocket thực tế | Veetee Server | firmware `WebsocketProtocol` | proposed → **implemented M1.4** |

M1.4 đã triển khai responder tối thiểu tại `/api/v1/devices/ota/check` (GET/POST/OPTIONS):
trả `websocket.url`, `websocket.token`, `websocket.version`, `server_time` và `firmware`
no-update. Đặc tả request/response đầy đủ nằm ở
[protocols-and-apis.md](protocols-and-apis.md#ota-config-discovery-veetee-quyết-định-veetee---m14)
và golden vector tại `../contracts/device/ota_check_request.json` /
`ota_check_response.json`. M5 mới bổ sung activation/binding/release/rollout đầy đủ.

### Chi tiết tương thích baseline đã xác minh ở M1.4

- `server_time.timestamp` phải là **epoch milliseconds**: firmware baseline
  `main/ota.cc:194-205` đọc `timestamp` là number và tính `tv_sec = ts / 1000`; gửi giây
  sẽ làm clock thiết bị sai về ~1970. `timezone_offset` là phút (firmware cộng
  `offset * 60 * 1000` vào timestamp ms).
- `firmware.version`/`firmware.url` rỗng là trạng thái no-update an toàn: firmware chỉ set
  `has_new_version_` khi cả hai là string và `IsNewVersionAvailable(current, new)` true
  (`ota.cc:225-237`); version rỗng parse thành 0 component nên không thể lớn hơn version
  thiết bị.
- POST có body phải `Content-Type: application/json` (firmware baseline luôn gửi header
  này trong `SetupHttp`, `ota.cc:69`); body rỗng chấp nhận.
- Các header bổ sung firmware gửi (`Activation-Version`, `Serial-Number`) được bỏ qua an
  toàn ở M1.4; `User-Agent`, `Accept-Language` chỉ giới hạn kích thước.
- Firmware ghi toàn bộ key string/number trong object `websocket` vào NVS
  (`ota.cc:169-183`); server chỉ trả đúng allowlist `url`, `token`, `version`.

## 2. WebSocket handshake

### Hành vi quan sát

Firmware mở URL được lưu trong `websocket.url` và gửi header:

| Header | Giá trị |
| --- | --- |
| `Authorization` | token từ settings; nếu chưa có khoảng trắng thì firmware thêm `Bearer ` |
| `Protocol-Version` | số version từ settings, mặc định runtime là 1 |
| `Device-Id` | MAC vật lý |
| `Client-Id` | UUID từ board/NVS |

Bằng chứng: `main/protocols/websocket_protocol.cc:80-106`.

Sau khi TCP/TLS WebSocket connected:

1. Device gửi một JSON `hello`.
2. Device chờ JSON server có `type=hello` và `transport=websocket`.
3. Timeout chờ server hello là 10 giây tại `websocket_protocol.cc:181-188`.
4. Nếu có `session_id`, firmware lưu lại.
5. Nếu có `audio_params.sample_rate` hoặc `frame_duration`, firmware cập nhật thông số
   downlink tương ứng.

### Hello từ device

Các field quan sát từ `websocket_protocol.cc:198-221`:

```json
{
  "type": "hello",
  "version": 1,
  "features": {
    "mcp": true,
    "aec": true
  },
  "transport": "websocket",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

`aec`, font/glyph capability và các feature khác là optional theo build. Server không
được giả định mọi firmware đều có chúng.

### Hello từ server

Server phải trả tối thiểu:

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "<opaque-session-id>",
  "audio_params": {
    "format": "opus",
    "sample_rate": 24000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

`session_id` phải opaque, random đủ mạnh, giới hạn kích thước và gắn với đúng device
connection. Server phải validate audio params thay vì echo mù giá trị từ device.

## 3. Wire message matrix

| Hướng | `type`/frame | Field hoặc semantic cần tương thích | M1/M2 |
| --- | --- | --- | --- |
| Device -> server | JSON `hello` | version, features, transport, audio_params | M1 |
| Device -> server | JSON `listen` | state `start/stop/detect`, mode `auto/manual/realtime` | M1/M2 |
| Device -> server | JSON `abort` | reason, session_id | M1/M2 |
| Device -> server | JSON `mcp` | JSON-RPC 2.0 payload | M1 envelope, M3 tools |
| Device -> server | binary | Opus raw/envelope theo protocol version | M1/M2 |
| Server -> device | JSON `hello` | transport, session_id, audio_params | M1 |
| Server -> device | JSON `stt` | transcript text, session_id | M1 fake/M2 ASR |
| Server -> device | JSON `llm` | optional emotion/UI metadata | M2 optional |
| Server -> device | JSON `tts` | `start`, `sentence_start`, `stop` | M1 fake/M2 TTS |
| Server -> device | JSON `mcp` | JSON-RPC initialize/list/call/result | M3 |
| Server -> device | JSON `alert/system` | status/control theo allowlist | M5+ |
| Hai chiều | JSON `ping/goodbye` | heartbeat/close semantic | M1 |

Các field required/optional, error envelope, size limit và version chính thức sẽ được
ghi trong golden vector sau M0.2. Không dùng cách upstream echo malformed JSON; Veetee
phải reject an toàn và không log raw payload.

## 4. Binary audio compatibility

### Protocol version quan sát

| Version | Wire format | Endianness/metadata | Mức ưu tiên |
| --- | --- | --- | --- |
| 1 | Raw Opus WebSocket binary frame | Không có header | P0 để kết nối baseline |
| 2 | `version`, `type`, `reserved`, `timestamp`, `payload_size`, payload | integer network byte order | P1 nếu cần server AEC |
| 3 | `type`, `reserved`, `payload_size`, payload | `payload_size` network byte order | P1 nếu baseline dùng |

Bằng chứng: `main/protocols/protocol.h:17-31` và
`main/protocols/websocket_protocol.cc:108-140`. Firmware decode v2/v3 trước khi chuyển
payload vào callback audio; v1 chuyển toàn bộ binary frame thành Opus payload.

### Contract audio tạm thời

| Hướng | Format | Sample rate | Channels | Frame duration |
| --- | --- | ---: | ---: | ---: |
| Device -> server | Opus | 16 kHz | 1 | 60 ms |
| Server -> device | Opus | 24 kHz mặc định tham khảo | 1 | 60 ms |
| Pipeline server VAD/ASR | PCM signed 16-bit | 16 kHz | 1 | provider/config |
| Gemini TTS native | PCM signed 16-bit | 24 kHz dự kiến | 1 | provider chunk |

Đây là compatibility target để spike/test, chưa phải wire contract cuối. Server phải
decode đúng version, giới hạn payload trước parse, kiểm tra truncated frame và giới hạn
queue theo byte/thời lượng. Không dùng timestamp giả nếu protocol version không cung cấp.

## 5. State, timeout và reconnect

### Hành vi quan sát

```text
network connected
  -> activation/discovery
  -> idle
  -> connecting
  -> hello exchange
  -> listening
  -> speaking
  -> listening hoặc idle
  -> close/reconnect
```

- Server hello timeout phía firmware: 10 giây.
- Khi WebSocket disconnect, firmware gọi close callback và quay về lifecycle app; không
  có resume conversation state được đảm bảo.
- `listen/start` bắt đầu gửi audio; `listen/stop` kết thúc capture; `listen/detect` báo
  wake word local.
- `tts/start` làm device dừng gửi microphone và vào speaking; `tts/stop` kết thúc lượt.
- Device có thể gửi `abort` khi wake word/barge-in.

### Yêu cầu Veetee

- Mỗi connection có session ID riêng; reconnect tạo session mới trong M1/M2.
- `turn_id` và `generation_id` của server là nội bộ, không bắt firmware hiểu field mới.
- Abort phải hủy ASR/LLM/TTS và loại stale audio/token trước khi nhận lượt mới.
- Timeout connect, hello, idle, provider và total turn là config typed; không hardcode trong
  WebSocket handler.
- Ping/pong, close code, retry/backoff và duplicate/out-of-order policy phải có test vector.

## 6. MCP compatibility boundary

Firmware tham khảo công bố `features.mcp=true` và server có thể gửi JSON-RPC 2.0 qua
`type=mcp`. M0 chỉ cần giữ envelope và correlation ID. M3 mới triển khai:

- `initialize`.
- `tools/list` có pagination.
- `tools/call` có schema validation.
- Result/error gắn đúng device/session/request.
- User-only tool phải có confirmation/authorization riêng.

Không để model gọi trực tiếp socket hoặc tự vượt allowlist. MCP tool output là untrusted
data trước khi đưa vào prompt.

## 7. Golden vectors và kiểm thử còn thiếu

M0.2 phải tạo fixture không chứa token/credential cho tối thiểu:

1. OTA check có websocket config, server time và không có firmware update.
2. OTA check có activation code/challenge.
3. OTA response malformed, field sai type, URL sai scheme, payload quá lớn.
4. WebSocket hello hợp lệ v1, v2 và feature optional.
5. Server hello tối thiểu, thiếu transport, sai transport và audio params ngoài allowlist.
6. Raw Opus v1, v2/v3 header hợp lệ, truncated header, payload length mismatch.
7. listen start/stop/detect, abort duplicate và session ID sai.
8. TTS start/sentence_start/stop cùng binary audio.
9. Disconnect trước/sau hello, timeout 10 giây và reconnect.
10. MCP envelope hợp lệ, JSON-RPC error, notification không có ID và response trễ.

**Kết luận M0.1:** đã đủ bằng chứng để bắt đầu M0.2 khóa namespace và tạo golden vector.
Chưa được coi là contract cuối, chưa viết server production và chưa test hardware E2E.

## Source đối chiếu

- Firmware baseline: `d6f6b642977940b862f6f3026c3915df75d388b6`.
- `references/xiaozhi-esp32/main/ota.cc:46-243`.
- `references/xiaozhi-esp32/main/application.cc:275-355,417-520`.
- `references/xiaozhi-esp32/main/protocols/websocket_protocol.cc:80-250`.
- `references/xiaozhi-esp32/main/protocols/protocol.h:10-87`.
- `references/xiaozhi-esp32/docs/websocket.md:7-329,331-449`.
