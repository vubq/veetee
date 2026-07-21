# Đặc tả veetee-firmware

## 1. Hardware profile V1

Profile dưới đây lấy từ `references/xiaozhi-esp32/main/boards/bread-compact-wifi-lcd/config.h` và `compact_wifi_board_lcd.cc`. Đây là baseline để chạy nhanh, không phải pin map đã được xác nhận cho mọi module N16R8.

| Chức năng | GPIO tham chiếu | Ghi chú |
|---|---:|---|
| INMP441 WS/LRCLK | 4 | RX I2S |
| INMP441 SCK/BCLK | 5 | RX I2S |
| INMP441 SD/DIN | 6 | INMP441 L/R phải nối đúng slot |
| MAX98357A DIN | 7 | TX I2S |
| MAX98357A BCLK | 15 | TX I2S |
| MAX98357A LRC/WS | 16 | TX I2S |
| ST7789 MOSI | 47 | SPI3 |
| ST7789 SCLK | 21 | SPI3 |
| ST7789 DC | 40 | command/data |
| ST7789 RST | 45 | reset |
| ST7789 CS | 41 | chip select |
| ST7789 backlight | 42 | PWM |
| BOOT/assistant control | 0 | bật/ngắt assistant; optional PTT, cần tránh giữ khi reset |
| LED | 48 | tùy module có LED thật |

### Điện và layout

- INMP441 và MAX98357A dùng chung GND; kiểm tra mức logic 3.3 V.
- MAX98357A cần nguồn/decoupling theo module, không kéo loa trực tiếp từ GPIO.
- INMP441 L/R phải nối GND hoặc VDD theo slot mà driver chọn; test bằng tone/FFT trước khi chạy ASR.
- GPIO 47/48 và 45 phải xác nhận khả dụng trên module cụ thể; không giả định mọi board S3 expose giống nhau.
- Octal flash/PSRAM N16R8 dùng chân nội bộ theo package; kiểm tra schematic module trước khi chọn pin bổ sung.
- Chụp lại pin map thật và lưu thành `veetee-firmware/boards/veetee-s3-n16r8/README.md` trước khi freeze.

## 2. Firmware structure

```text
veetee-firmware/
├── main/
│   ├── app/              # event loop, state machine, commands
│   ├── board/            # interface + veetee-s3-n16r8 implementation
│   ├── audio/            # I2S, Opus, VAD, wake word, AEC hooks
│   ├── display/          # ST7789/LVGL or lightweight display adapter
│   ├── network/          # Wi-Fi station/AP provisioning
│   ├── transport/        # protocol-neutral + WebSocket v1
│   ├── mcp/              # device MCP registry/dispatcher
│   ├── ota/              # bootstrap, activation, signed executable OTA
│   ├── config/           # desired/reported snapshot, reconcile, apply journal
│   ├── resources/        # signed wake/model/assets bundle, A/B slot loader
│   └── settings/         # NVS schema/version/migration
├── boards/veetee-s3-n16r8/
└── partitions/
```

## 3. State machine

```text
STARTING
  ├─ no credentials ───────> WIFI_CONFIGURING
  ├─ station timeout ──────> WIFI_CONFIGURING
  └─ network connected ────> ACTIVATING

WIFI_CONFIGURING
  └─ credentials accepted ──> ACTIVATING

ACTIVATING
  ├─ activation pending ────> ACTIVATING (retry/backoff)
  ├─ OTA update ────────────> UPGRADING -> reboot
  └─ bootstrap complete ────> IDLE

IDLE/STANDBY
  ├─ button wake ───────────> CONNECTING -> LISTENING (mode=auto, source=button)
  ├─ activation wake word ──> CONNECTING -> LISTENING (mode=auto, source=wake_word)
  ├─ config invalidation ───> RECONCILING -> IDLE/STANDBY
  └─ config command ────────> WIFI_CONFIGURING

RECONCILING
  ├─ download/verify/stage ─> APPLYING_RESOURCES -> IDLE/STANDBY
  ├─ incompatible/invalid ──> IDLE/STANDBY (giữ active version cũ)
  └─ button/wake priority ──> pause apply -> LISTENING

LISTENING
  ├─ VAD final ─────────────> EVALUATING (input admission + semantic gate)
  ├─ input rejected ────────> LISTENING (không gọi LLM/MCP)
  ├─ button off ────────────> IDLE/STANDBY, không tạo AI turn
  └─ inactivity timeout ────> CLOSING

EVALUATING
  ├─ non-actionable/unclear -> LISTENING hoặc hỏi lại theo policy
  ├─ question/request ──────> THINKING
  ├─ conversation.end ──────> CLOSING
  └─ button/interrupt word ─> ABORTING -> LISTENING

THINKING
  ├─ server TTS start ──────> SPEAKING
  ├─ MCP tool ──────────────> THINKING (tool deadline/cancel thuộc server)
  └─ button/interrupt word ─> ABORTING -> LISTENING

SPEAKING
  ├─ button/interrupt word ─> ABORTING -> LISTENING
  ├─ semantic/AEC barge-in ─> ABORTING -> LISTENING
  ├─ tts stop auto mode ────> LISTENING (assistant gate còn mở)
  └─ tts stop manual mode ──> IDLE

CLOSING
  ├─ goodbye complete ──────> IDLE/STANDBY
  └─ button/wake/speech ────> ABORTING -> LISTENING
```

State transition phải chỉ đi qua một state machine; callback Wi-Fi/WebSocket chỉ post event. Firmware không được đổi state trực tiếp từ network callback.

## 4. Wi-Fi provisioning

### Boot path

1. Đọc NVS credentials và `wifi_config_version`.
2. Không có credentials: bật AP `Veetee-XXXX`, captive portal `/wifi`.
3. Có credentials: thử station trong 60 giây.
4. Timeout hoặc auth failure liên tiếp: stop station, bật AP.
5. Save credentials atomically; restart station; chỉ khi DHCP thành công mới chạy activation.
6. Khi user giữ BOOT 5 giây trong `idle`, reset credentials có xác nhận âm thanh/hình ảnh rồi quay lại AP.

AP portal cần có:

- scan SSID + RSSI;
- password và optional hidden SSID;
- server/OTA URL override trong chế độ dev;
- locale và wake profile;
- nút test kết nối và reset.

Không lưu password plain text vào log. NVS namespace có version/migration và nút factory reset riêng.

## 5. Bootstrap và activation 6 số

### Request

`POST /veetee/ota/` (giữ path alias `/xiaozhi/ota/` khi compatibility mode) với:

- `Device-Id`: MAC chuẩn hóa;
- `Client-Id`: UUID bền vững;
- `Device-Model`: `veetee-s3-n16r8`;
- `Firmware-Version` và system info JSON;
- `Accept-Language: vi-VN`.

### Response tối thiểu

```json
{
  "server_time": {"timestamp": 1760000000000, "timezone_offset": 420},
  "activation": {
    "code": "482913",
    "message": "Mở Veetee Manager và nhập mã 482913",
    "challenge": "device-challenge-id",
    "expires_at": "2026-07-21T12:00:00Z"
  },
  "websocket": {"url": "wss://voice.example/veetee/v1/", "token": "..."},
  "firmware": {"version": "0.1.0", "url": ""},
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

### Binding flow

1. Device đọc `activation.code`, hiện mã bằng màn hình + audio digits.
2. User mở manager, chọn agent, nhập code.
3. API kiểm tra Redis key theo code, MAC/challenge, TTL và user permission.
4. API ghi device ownership + agent binding trong transaction.
5. API xóa code/data keys (one-time use), phát audit event.
6. Device gọi `POST /veetee/ota/activate` với `Device-Id`, `Client-Id`, challenge proof nếu bật.
7. Server trả 200; firmware xóa activation cache, khởi tạo protocol và vào `idle`.

MVP có thể tương thích code 6 số hiện tại. Production phải thêm `expires_at`, attempt counter, CSPRNG, HTTPS bắt buộc và signature/challenge proof.

## 6. Button, wake word và exit phrase

### Button profile

- Click khi `idle/standby`: bật assistant gate, mở channel và `listen:start`, mode `auto`.
- Khi `listening`, VAD tự kết thúc lượt nói; không cần click lần hai để AI trả lời.
- Click khi `evaluating/thinking/speaking/closing`: gửi `abort`, clear decoder/queues và chuyển về `listening` nếu gate còn mở.
- Long press khi `listening`: tắt assistant gate, gửi `listen:stop` với reason `user_disable`, không yêu cầu AI trả lời và về standby.
- Press-and-hold PTT là compatibility/accessibility mode tùy chọn, down=start và up=stop; không phải mode mặc định.
- Giữ 5 giây ở `idle`: vào Wi-Fi config, không gọi server.

### Wake profile

Wake/interrupt detector phải là model local có profile cấu hình, không phải tìm chuỗi bằng `if/else` trong transcript. Có hai vai trò riêng:

- activation profile: gọi robot dậy từ standby;
- interrupt profile: ngắt AI đang evaluating/thinking/speaking/closing.

Cấu hình profile ví dụ:

```json
{
  "id": "wake-vi-home-v4",
  "locale": "vi-VN",
  "activation": {
    "examples": ["veetee ơi", "chào veetee"],
    "model_pack_artifact_id": "model:esp-sr-vi-home:3.0.1",
    "detector_id": "wakenet:veetee_vi",
    "sensitivity": 0.62
  },
  "interrupt": {
    "examples": ["dừng lại", "không nói nữa"],
    "model_pack_artifact_id": "model:esp-sr-vi-home:3.0.1",
    "detector_id": "multinet:interrupt_stop",
    "sensitivity": 0.7
  },
  "send_wake_audio": false
}
```

Activation/interrupt phrase và exit phrase không được xử lý bằng một chuỗi `if/else` hard-code trong firmware. Local detector dùng signed model/config profile vì phải hoạt động khi chưa mở cloud session hoặc cần abort tức thời. Exit intent và các interrupt diễn đạt tự do do ASR + intent model/LLM trên server suy luận theo ngữ cảnh khi audio path cho phép. Các câu trong profile chỉ là examples/training/config data, không phải toàn bộ ngôn ngữ được hỗ trợ.

Runtime wake V1 trên ESP32-S3 là ESP-SR: WakeNet cho activation hoặc MultiNet cho command/interrupt tùy model pack đã build. Firmware load một `srmodels.bin` tương thích resource ABI; `detector_id` chỉ chọn detector/command đã tồn tại trong model pack, không tải native operator tùy ý. Runtime khác như `sherpa-onnx` chỉ được thêm bằng firmware OTA và ADR sau khi benchmark CPU/RAM/latency trên board thật.

Button interrupt là guarantee của V1. Interrupt bằng giọng nói khi loa đang phát chỉ là best-effort cho tới khi audio path có far-end playback reference và AEC benchmark pass; firmware/UI không được quảng bá full-duplex trước gate này.

## 7. Input admission và conversation timeout

Firmware chịu trách nhiệm thu audio sạch nhất có thể và gửi metadata signal; server chịu trách nhiệm quyết định input có tạo AI turn hay không. Firmware không hard-code loại nguồn âm thanh cụ thể.

Metadata có thể gồm VAD probability, RMS/noise floor, clipping, frame loss và optional AEC state. Server trả state/telemetry tổng quát như `non_actionable`, `unclear`, `accepted` để UI hiển thị; firmware không cần biết input đến từ TV, quạt hay nguồn cụ thể nào.

Sau mỗi response, firmware giữ assistant gate mở và chạy watchdog session đồng bộ với server. `first_input_timeout`, `between_turns_timeout` và `closing_grace` nhận từ signed agent/bootstrap config trong safe range. Khi server gửi sleep/close:

1. phát goodbye TTS nếu có;
2. trong closing grace vẫn ưu tiên button/activation/interrupt event;
3. nếu không có input mới, dừng upload audio và về standby;
4. nếu server/socket treo quá hard deadline, firmware tự clear queue, đóng channel và về standby.

## 8. Dynamic config và resource bundle

Wake profile, interrupt profile, timeout, policy, model và assets có thể cấu hình từ Manager Web/API theo `docs/12-dynamic-config-and-artifacts.md`.

Firmware giữ:

- `desired_config_version` và `applied_config_version`;
- `desired_resource_version` và `active_resource_version`;
- capability/runtime ABI;
- inactive/active resource slot pointer;
- apply journal có CRC để recover khi mất điện.

Luồng reconcile:

1. Nhận optional `config_changed` invalidation hoặc kiểm tra bootstrap định kỳ.
2. Pull config/manifest bằng HTTP(S) với ETag; không nhận binary qua WebSocket.
3. Validate device target, schema, safe bounds, size, runtime ABI, hash và signature.
4. Download bundle vào inactive resource slot; active slot không bị overwrite.
5. Verify toàn bộ, load smoke test và stage.
6. Apply ở standby/session boundary; wake/button luôn có priority.
7. Report desired/reported state và health window.
8. Rollback slot/profile cũ nếu load/crash/watchdog/detector health fail.

Resource bundle chỉ chứa model/assets/data. Code native, model operator hoặc runtime mới phải cập nhật bằng signed firmware OTA. NVS không dùng để chứa binary model lớn.

Partition strategy ưu tiên của V1 là executable A/B và resource A/B vì đơn giản, dễ recover và dễ kiểm thử mất điện. Size cụ thể chỉ freeze sau khi đo firmware thật có ESP-SR/LVGL/Opus/HTTPS/MCP. Nếu resource slot không đủ cho scope assets đã chốt, phải mở ADR để chọn resource store 8 MB hoặc giảm asset scope; không tự đổi partition sau khi code đã phụ thuộc layout. Manager và firmware đều phải từ chối bundle vượt inactive slot.

## 9. Audio/realtime defaults

- Mic PCM: 16 kHz, mono, signed 16-bit.
- Speaker decode: 24 kHz, mono.
- Compatibility frame: Opus 60 ms.
- Low-latency profile sau khi có fixture: 20 ms hoặc 40 ms, vẫn dùng field `frame_duration` trong hello.
- Queue mọi frame có deadline; drop có metric thay vì block vô hạn.
- `abort` phải idempotent và hoàn tất trong mục tiêu <100 ms ở local device.
- Khi local abort, firmware đặt `accept_tts_audio=false`, clear decoder/playback queue và chỉ nhận binary TTS lại sau `tts:start` của generation mới. Raw Opus V1 không mang `turn_id`, nên quy tắc này là bắt buộc để frame cũ đang nằm trong socket không phát lại.

## 10. Definition of Done firmware V1

- Boot lần đầu vào AP và lưu Wi-Fi thành công.
- Wi-Fi sai/không có router tự quay lại AP.
- OTA bootstrap nhận websocket URL/token và mã 6 số.
- Bind trên manager xong thì firmware activate và reconnect.
- WebSocket hello + raw Opus + JSON events pass contract tests.
- Auto conversation, assistant gate và abort không kẹt state, không nghe lại audio cũ.
- Cả button wake và activation wake word đều mở cùng một `mode=auto` flow.
- Button luôn hủy evaluating/LLM/TTS/MCP turn và quay lại listening. Interrupt profile phải pass ở standby/thinking; khi speaking là best-effort cho tới khi AEC gate pass.
- Input bị admission gate từ chối không tạo LLM/MCP call và không làm robot trả lời vô cớ.
- Conversation inactivity timeout phát goodbye theo config rồi về standby; wake trong closing grace phải mở lại listening.
- Wake sensitivity/profile/model và assets đổi được qua signed config/resource bundle mà không build firmware nếu runtime ABI không đổi.
- Firmware pull/verify/stage/apply/report desired/reported state; signature/size/ABI lỗi giữ version cũ.
- Mất điện trong download/apply không phá active resource; rollback resource slot pass trên board thật.
- Button wake vẫn hoạt động nếu wake model mới load hoặc health check thất bại.
- Người dùng nói xong thì VAD tự finalize và AI trả lời mà không cần click lần hai.
- ST7789 hiển thị locale Việt, UTF-8 fallback hợp lý.
- MCP `initialize/tools/list/tools/call` pass với ít nhất 3 device tools.
- OTA signed artifact update và rollback test pass trên board thật.
