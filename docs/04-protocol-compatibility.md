# Protocol compatibility contract

Mục tiêu của `veetee` là giữ các field và semantics mà firmware Xiaozhi đã triển khai, đồng thời tách contract thành fixture có version. Compatibility mode dùng path `/xiaozhi/...`; native mode dùng `/veetee/...` nhưng body/headers giống nhau.

## 1. WebSocket handshake

### Headers

```http
Authorization: Bearer <token>
Protocol-Version: 1
Device-Id: AA:BB:CC:DD:EE:FF
Client-Id: 2db0f1c7-...
```

Server phải validate device id, token scope, protocol version và max frame trước khi tạo `Session`.

### Device hello

```json
{
  "type": "hello",
  "version": 1,
  "features": {"mcp": true, "aec": false, "glyph_push": false},
  "transport": "websocket",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

### Server hello

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "01J...",
  "audio_params": {
    "format": "opus",
    "sample_rate": 24000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

Server phải gửi hello trong 10 giây. Nếu client quảng cáo `aec=true`, server có thể bật timestamped binary protocol v2 và AEC policy; không tự bật nếu audio profile chưa test.

Thứ tự handshake V1 là bắt buộc: HTTP upgrade thành công -> device gửi `hello` ->
server validate -> server gửi `hello`. Server không được chủ động gửi hello trước
device. JSON hello/control tối đa 8 KiB; binary trước device hello, JSON malformed,
hello sai audio profile hoặc `session_id` không khớp phải bị đóng bằng WebSocket
protocol/policy code rõ ràng thay vì tiếp tục session ở trạng thái mơ hồ.

## 2. JSON event contract

Mọi event trong audio session có `session_id` trừ hello.

| Hướng | Type | Field bắt buộc | Ý nghĩa |
|---|---|---|---|
| Device -> server | `listen` | `state`, optional `mode/source/reason` | start/stop/detect capture |
| Device -> server | `abort` | optional `reason/source` | dừng ASR/LLM/TTS/MCP turn hiện tại |
| Device -> server | `mcp` | `payload` | JSON-RPC 2.0 |
| Server -> device | `stt` | `text` | transcript user |
| Server -> device | `tts` | `state` | start/sentence_start/stop |
| Server -> device | `llm` | `emotion`, optional `text` | UI emotion/metadata |
| Server -> device | `mcp` | `payload` | tool request/response |
| Server -> device | `system` | `command` | reboot hoặc command allowlist |
| Server -> device | `alert` | `status`, `message`, `emotion` | thông báo UI/audio |
| Server -> device | `custom` | `payload` | optional feature flag |

### Listen modes

- `auto`: mode mặc định của Veetee. Button/wake chỉ mở assistant gate; VAD quyết định hết câu, server tự chạy ASR -> LLM -> TTS và sau `tts.stop` quay lại listening khi gate còn mở.
- `manual`: compatibility/accessibility mode; firmware/button gửi stop và sau `tts.stop` về idle. Không dùng làm trải nghiệm mặc định.
- `realtime`: full duplex/AEC; chỉ bật nếu cả hai phía quảng cáo và test pass.

Việc kết thúc capture trong `auto` là event VAD/server, không phải một nút “gửi câu hỏi”. Nút có thể gửi `abort` hoặc `listen:stop` với reason điều khiển assistant gate, nhưng không quyết định lúc AI được phép trả lời.

Hai nguồn đánh thức phải hội tụ vào cùng flow:

```jsonl
{"type":"listen","state":"start","mode":"auto","source":"button"}
{"type":"listen","state":"detect","text":"Hey VeeTee","source":"wake_word"}
{"type":"listen","state":"start","mode":"auto","source":"wake_word"}
```

`source` là metadata optional để trace/policy, không tạo hai conversation implementation khác nhau.

### Abort semantics

`abort` là idempotent. Server phải cancel ASR/LLM/TTS/MCP/tool tasks của turn hiện tại, gửi `tts.stop` nếu client đang speaking, rồi chấp nhận `listen:start` mới ngay lập tức. Không được chờ provider timeout.

`reason` là forward-compatible reason code. V1 phải chấp nhận `wake_word_detected` của Xiaozhi và các native code `button_interrupt`, `local_interrupt_detected`, `semantic_interrupt`, `new_turn`, `session_closing_cancelled`. Parser không được reject một abort hợp lệ chỉ vì reason mới chưa biết. Đây là telemetry/policy metadata, không phải exact phrase.

Raw Opus V1 không chứa `turn_id`. Sau local abort, firmware phải ngừng nhận binary TTS, clear decoder/playback queue và chỉ mở lại audio gate sau `tts:start` mới. Server phải generation-check trước từng send; kết quả/frame cũ bị drop dù provider không cancel kịp. WebSocket text/binary ordering trong một connection được coi là protocol invariant.

### Conversation timeout

Không thêm wire type mới nếu chưa cần. Server dùng event tương thích hiện có:

```json
{
  "type": "system",
  "session_id": "01J...",
  "command": "assistant_sleep",
  "reason": "inactivity_timeout"
}
```

Goodbye audio vẫn đi theo `tts:start` -> binary Opus -> `tts:stop`. Trong closing grace, button/wake event tạo `abort` rồi `listen:start(mode=auto)`. Firmware phải bỏ qua unknown optional field nhưng không bỏ qua `command` đã support.

### Dynamic config và artifact delivery

Bootstrap giữ các field Xiaozhi cũ và có thể thêm optional `config`/`resources` object. Device không tải binary qua WebSocket:

```json
{
  "config": {
    "version": 13,
    "etag": "agent-config-13",
    "url": "http://192.168.1.20:8003/veetee/config/v1/devices/AA-BB"
  },
  "resources": {
    "version": "1.4.0",
    "manifest_url": "http://192.168.1.20:8003/veetee/artifacts/manifests/01JRESOURCE"
  }
}
```

Firmware phải validate schema/target/version/size/hash/signature/ABI rồi mới stage. Resource bundle được tải qua HTTP range/resume nếu có, ghi inactive slot và activate atomically. WebSocket chỉ gửi invalidation metadata:

```json
{
  "type": "system",
  "session_id": "01J...",
  "command": "config_changed",
  "config_version": 13,
  "resource_version": "1.4.0"
}
```

`config_changed` không chứa URL tùy ý hoặc binary. Device dùng bootstrap trust và URL allowlist để pull; khi đang voice turn, reconcile được hoãn tới session boundary.

## 3. Binary protocol

### V1 raw Opus

Mỗi binary WebSocket frame là một Opus packet. Server tự biết sample rate/frame duration từ hello.

Server packetize một TTS stream liên tục: giữ phần PCM dư giữa các provider chunk,
chỉ zero-pad frame cuối trước `tts:stop`. Sender được phép gửi một prebuffer nhỏ rồi
pace theo `frame_duration`; không được burst toàn bộ câu trả lời làm tràn playback
queue của firmware. Baseline LAN dùng ba frame prebuffer và nhịp 60 ms.

Thứ tự response V1 là `stt` -> một hoặc nhiều `llm` metadata -> `tts:start` ->
binary Opus -> `tts:stop`. `admission`, `plan`, `text_delta` và generation nội bộ
không phải wire type; chúng phải được adapter map về event tương thích ở biên
WebSocket.

### V2 timestamped

```c
struct BinaryProtocol2 {
  uint16_t version;       // network byte order, 2
  uint16_t type;          // 0 = OPUS, 1 = JSON
  uint32_t reserved;
  uint32_t timestamp_ms;
  uint32_t payload_size;
  uint8_t payload[payload_size];
};
```

### V3 compact

```c
struct BinaryProtocol3 {
  uint8_t type;
  uint8_t reserved;
  uint16_t payload_size;
  uint8_t payload[payload_size];
};
```

Server phải kiểm tra kích thước header/payload trước khi decode; không trust `payload_size`. Firmware phải drop malformed frame, không crash/reset.

## 4. MQTT + UDP profile

MQTT giữ hello/control; UDP giữ audio packet encrypted. Packet wire giữ 16-byte header và AES-CTR/sequence semantics của Xiaozhi. `veetee` không tự đổi crypto trong profile tương thích; nếu cần thay, tạo protocol version mới và migration gateway.

## 5. OTA/bootstrap contract

Hai endpoint logic:

- `POST /xiaozhi/ota/` (alias `/veetee/ota/`) - report system info, nhận config/activation/firmware.
- `POST /xiaozhi/ota/activate` (alias `/veetee/ota/activate`) - hoàn tất activation.

Bootstrap nhận `Device-Id`, optional-compatible `Client-Id`, model, firmware và locale.
Khi chưa bind, `activation` có code 6 số, challenge, TTL; retry cùng hardware trong
TTL phải trả cùng ticket thay vì sinh vô hạn code. `websocket.token` được phép rỗng
ở trạng thái này và device chưa được mở voice session. Sau khi Manager atomically
consume code, firmware poll activate bằng challenge; server trả `202` tới khi bind
xong và kết quả `200` phải idempotent để retry sau mất response không xoay token.

Sau activation, bootstrap yêu cầu Bearer device token, không còn `activation` và có
thể trả `config`/`resources` optional. Device-facing JSON dùng `snake_case`; URL lấy
từ cấu hình LAN/public endpoint, không nhúng domain cố định vào firmware.

Firmware phải parse được cả `websocket` và `mqtt` object để giữ compatibility. Native Veetee V1 chọn WebSocket mặc định và chỉ chọn MQTT+UDP khi signed `preferred_transport=mqtt_udp` được publish rõ cho device/agent. Compatibility profile có thể giữ hành vi ưu tiên MQTT giống source tham chiếu. Server không được vô tình làm native firmware đổi transport chỉ vì response có thêm `mqtt` object.

## 6. MCP envelope

```json
{
  "session_id": "01J...",
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "id": 12,
    "method": "tools/call",
    "params": {
      "name": "self.audio_speaker.set_volume",
      "arguments": {"volume": 55}
    }
  }
}
```

Supported request methods for V1:

- `initialize`;
- `tools/list` with `cursor` and `withUserTools`;
- `tools/call`.

Tool result uses `result.content[]` and `isError`; error uses JSON-RPC `error.code/message`. Firmware giới hạn payload khoảng 8 KB nên server phải follow `nextCursor`.

## 7. Compatibility test matrix

Mỗi release chạy fixture hai chiều:

| Fixture | Firmware V1 | Server V1 | Expected |
|---|---:|---:|---|
| hello + session | yes | yes | open within 10s |
| raw Opus upload/download | yes | yes | decode/play |
| listen auto/manual/realtime | yes | yes | auto is default, correct state |
| abort while ASR/LLM/TTS/MCP | yes | yes | no stale result/audio; tool not dispatched or audited as `completed_after_abort` |
| button wake + wake-word detect | yes | yes | same auto flow, new turn |
| input admission reject | n/a | yes | no LLM/MCP call, remain listening |
| inactivity timeout + closing grace | yes | yes | goodbye, sleep or cancel closing |
| config/resource manifest signature | yes | yes | verify before stage |
| config_changed invalidation | yes | yes | pull later, no binary over WS |
| resource A/B rollback | yes | yes | active slot remains usable |
| MCP pagination | yes | yes | all tools discovered |
| MCP user-only | yes | yes | hidden by default |
| OTA activation | yes | yes | bind then activate |
| malformed frames | yes | yes | drop + metric |

Contract changes require a fixture update, changelog entry và compatibility run trước khi merge.

## 8. Naming và route canonical

- Device-facing WebSocket/bootstrap/config/artifact JSON dùng `snake_case` để gần contract Xiaozhi và giảm mapping trên firmware.
- Manager REST/OpenAPI có thể dùng `camelCase`; DTO boundary phải map rõ sang device contract.
- Canonical native routes: `/veetee/v1/`, `/veetee/ota/`, `/veetee/config/v1/devices/:deviceId`, `/veetee/artifacts/manifests/:manifestId`, `/veetee/artifacts/:artifactId/content`.
- Compatibility aliases: `/xiaozhi/v1/`, `/xiaozhi/ota/`. Alias chỉ nằm ở gateway/transport layer.
- Bootstrap resource field canonical là `resources.manifest_url`; WebSocket invalidation dùng `config_version` và `resource_version`.
- Manifest/content GET gửi `Authorization: Bearer ...` và `Device-Id`; content
  support một range `bytes=N-`, trả `206`, exact `Content-Length`,
  `Content-Range` và `Accept-Ranges: bytes`. Redirect và compressed transfer bị từ chối.
- `audio_params.sample_rate` trong device hello là uplink mic rate; trong server hello là downlink TTS rate. Implementation không được coi hai giá trị này là cùng một hướng audio.
