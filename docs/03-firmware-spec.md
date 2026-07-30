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

Profile breadboard dùng ST7789 SPI mode 0 ở 10 MHz để ưu tiên signal integrity trên
dây Dupont. Clock có thể nâng sau khi panel thật pass color/orientation test; SPI
mode 3, backlight invert và geometry vẫn là board configuration, không phải nhánh
runtime tự đoán panel.

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
│   ├── maintenance/      # một executor tuần tự cho control-plane HTTP/TLS
│   └── settings/         # NVS schema/version/migration
├── boards/veetee-s3-n16r8/
└── partitions/
```

Bootstrap, device config, wake resource, UI Pack, executable OTA và
reported-state không sở hữu sáu task TLS thường trú. Chúng đăng ký sáu lane vào một
`MaintenanceExecutor` dùng chung: stack RAM nội 12 KiB, priority 3; mailbox, response
buffer và state lớn phù hợp nằm trong PSRAM. Executor chạy tuần tự theo thứ tự
`reporter -> firmware -> bootstrap -> device config -> wake resource -> UI Pack`;
mỗi owner vẫn giữ parser, generation, journal, A/B transaction và retry policy riêng.
Topology này chỉ thay đổi scheduling/memory ownership, không đổi bootstrap/OTA/MCP/
WebSocket wire contract.

Khi application chuyển từ idle sang một state hội thoại realtime, nó đóng gate trước,
ngắt HTTP deferrable đang chạy rồi chờ executor quiescent tối đa 150 ms trước khi mở
WebSocket. Budget được đo từ trước thao tác đóng socket, bao gồm cả close handshake và
handler cleanup; vượt budget phải log elapsed và từ chối voice TLS của lần đó.
Target/config/report bền vững không bị xóa và được chạy lại ở boundary idle
tiếp theo. Nếu cleanup không hoàn tất trong budget, firmware từ chối mở thêm voice TLS,
đi qua `transport_lost` và giữ recovery bounded thay vì chạy hai TLS workload chồng
nhau. Sau khi application đã vào exclusive `upgrading`, chỉ firmware OTA và durable
reporter được schedule; HTTP ghi firmware đang ở boundary này không bị voice preempt.
Resource/UI erase kiểm realtime gate giữa từng sector 4 KiB; không dùng một erase 64 KiB
đơn lẻ có thể giữ shared worker quá barrier.

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
  ├─ stored identity reject ─> PAIRING_RECOVERY
  ├─ OTA update ────────────> UPGRADING -> reboot
  └─ bootstrap complete ────> IDLE

UPGRADING
  ├─ signed image staged ───> reboot (application task owns reboot)
  ├─ update failed ─────────> ACTIVATING (authenticated bootstrap retry)
  └─ Wi-Fi lost ────────────> NETWORK_CONNECTING (cancel OTA generation)

PAIRING_RECOVERY
  ├─ hold button 5 seconds ─> WIFI_CONFIGURING (clear identity + provisioning)
  └─ short press/no input ──> PAIRING_RECOVERY (không tự xóa credential)

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
  ├─ transport retry ───────> CONNECTING (giữ gate, session V1 mới)
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
  ├─ transport retry ───────> CONNECTING (hủy playback/output cũ)
  ├─ tts stop auto mode ────> LISTENING (assistant gate còn mở)
  └─ tts stop manual mode ──> IDLE

CLOSING
  ├─ goodbye complete ──────> IDLE/STANDBY
  └─ button/wake/speech ────> ABORTING -> LISTENING
```

State transition phải chỉ đi qua một state machine; callback Wi-Fi/WebSocket chỉ post event. Firmware không được đổi state trực tiếp từ network callback.

ST7789 dùng renderer trạng thái bất đồng bộ với queue depth 1: event loop chỉ ghi
snapshot mới nhất, display task tự vẽ `starting`, `wifi_configuring`,
`network_connecting`, `activating`, `pairing_recovery`, `idle`, `connecting`,
`listening`, `evaluating`, `thinking`, `speaking`, `aborting`, `closing` và
`upgrading`. `upgrading` được append sau 13 state V1: built-in renderer có copy
`UPDATING / DO NOT POWER OFF`, còn signed UI Pack V1 cũ dùng style `activating` để
không đổi index/schema đã ký. Vẽ màn
hình hoặc activation code không được chặn button abort, network callback hay audio
hot path. Text boot/recovery tối thiểu có thể deterministic trong firmware; persona,
semantic response và nội dung hội thoại vẫn đến từ config/AI. Visual sản phẩm có
thể chuyển thành signed `display_assets` mà không thay state contract.

## 4. Wi-Fi provisioning

### Boot path

1. Đọc NVS schema V3 và record CRC chứa tối đa 5 Wi-Fi profile; firmware V2 được
   migrate atomically từ `wifi_ssid`/`wifi_pass` mà không làm mất activation.
2. Không có profile hợp lệ: bật AP `Veetee-XXXX`, captive portal tại
   `http://192.168.4.1`.
3. Có profile: scan AP gần đó, ghép theo SSID và xếp candidate bằng cả
   last-success/MRU lẫn RSSI. Mạng thấy được được thử trước; profile mạng ẩn hoặc
   tạm thời không xuất hiện vẫn được thử sau theo MRU.
4. Mỗi candidate chỉ có retry hữu hạn. Khi hết danh sách, firmware rescan có delay
   2.5 giây trong timeout chung 60 giây để tránh reconnect storm.
5. Chỉ khi DHCP thành công mới đánh dấu profile là last-success, tăng success
   counter và chạy activation/bootstrap. Khi mất mạng lúc đang online, firmware
   scan/xếp lại toàn bộ danh sách thay vì kẹt ở SSID cũ.
6. Nếu tất cả profile đều thất bại hoặc hết timeout: stop station rồi mới bật AP.
7. Save profile/config atomically; password rỗng của SSID đã biết nghĩa là dùng lại
   password đang lưu. Password rỗng của mạng mới chỉ hợp lệ cho mạng open.
8. Khi user giữ BOOT 5 giây trong `idle`, xóa toàn bộ profile Wi-Fi và bootstrap
   override, có xác nhận âm thanh/hình ảnh rồi quay lại AP.

AP portal cần có:

- scan SSID + RSSI;
- đánh dấu mạng đã lưu nhưng không bao giờ trả password về browser;
- password và optional hidden SSID;
- server/OTA URL override trong chế độ dev;
- locale và wake profile;
- time zone IANA của thiết bị (báo lại trong reported state để prompt/session ưu tiên);
- nút test kết nối và reset.

HTTP server task dùng stack 12 KiB trên ESP-IDF 6.0.2. Đây là budget đã đo từ lỗi
stack overflow trong đường `httpd_resp_send` của iOS captive webview; không hạ về
default 4 KiB nếu chưa chạy lại hardware portal stress test.

System event task dùng stack 4 KiB. Buffer scan station 32 AP và danh sách RSSI
phải nằm trong `WifiManager`, không đặt trên stack callback `WIFI_EVENT_SCAN_DONE`.
ESP-IDF default 2.304 byte đã được đo là overflow ngay sau khi lưu provisioning,
gây reboot loop và phát lại startup chime liên tục.

AP DHCP phải advertise chính `192.168.4.1` làm DNS. Không advertise trang HTML
trực tiếp qua DHCP option 114: option này dành cho Captive Portal API chuẩn hóa,
không phải login UI. Khi chưa triển khai CAPPORT API/TLS đúng chuẩn, firmware dùng
DNS wildcard và các probe URL riêng cho Apple/Android/Windows/Firefox.

HTTP scan không được chạy lúc portal vừa start. Firmware phải chờ client nhận IPv4
từ DHCP, sau đó mới cho request `/api/scan` khởi động scan bất đồng bộ và trả cache.
Không chạy full-channel scan định kỳ khi SoftAP đang phục vụ client vì radio channel
hopping có thể làm DHCP thất bại hoặc captive webview trắng rồi timeout. Trong config
mode, Wi-Fi power-save phải tắt để SoftAP phản hồi ổn định.

Firmware chỉ mở DNS/HTTP captive services sau khi `esp_netif` xác nhận DHCP server
đã ở trạng thái `STARTED`; thời gian chờ phải bounded và lỗi rõ ràng thay vì quảng bá
một portal chưa sẵn sàng.

Lifecycle Wi-Fi bám theo Xiaozhi: driver chỉ init một lần với Wi-Fi NVS nội bộ tắt;
station/AP default netif được tạo khi vào đúng mode, rồi Wi-Fi được stop và netif cũ
được destroy trước khi chuyển mode. DHCP provisioning giữ static-ARP unicast path
mặc định giống cấu hình Xiaozhi; tắt static ARP trên ESP-IDF 6.0.2 làm broadcast
OFFER/ACK trả `ERR_MEM` trên board này và không hoàn tất lease.
ESP32-S3 dùng cùng profile RX footprint nhỏ của Xiaozhi (`3` static RX, `6` dynamic
RX, BA window `3` và dynamic management RX). Profile này giữ đủ internal/DMA memory
để DHCP tạo dynamic TX buffer; cấu hình RX mặc định lớn hơn đã làm OFFER/ACK lỗi
`ERR_MEM` trên board thật.

Route `/` trả portal shell. Giống flow đã được kiểm chứng của Xiaozhi, các URL
connectivity-check trả `302 Found` tới
`http://192.168.4.1/?_=<monotonic-nonce>`. Cache-busting nonce buộc captive
WebView mở lại trang cấu hình trong route đã được OS bind với SoftAP, không dùng
trang trung gian hay phụ thuộc `meta refresh`. Mọi response đặt
`Connection: close` và timeout gửi/nhận 15 giây.

Chỉ các connectivity-check URL đã biết mới được redirect. Không redirect handler
404 tổng quát: request phụ như `/favicon.ico` nếu bị điều hướng về `/` có thể làm
Android captive WebView reload toàn trang, khóa thao tác bằng loading overlay rồi
tự rời AP. Favicon trả `204 No Content`; URL không biết dùng 404 mặc định.

Nếu người dùng thoát mà chưa lưu, firmware tiếp tục ở config mode. Khi station cuối
cùng rời SoftAP, firmware đóng các HTTP session còn treo, hủy scan đang chạy nhưng
giữ DHCP server hoạt động. Lần kết nối lại `Veetee-XXXX` phải nhận IP và kích hoạt
portal như một phiên độc lập; không cần reboot thiết bị và không persist dữ liệu form
chưa submit.

HTML, CSS và JavaScript phải được phục vụ thành resource riêng và gửi theo chunk tối
đa 1 KiB. Hardware trace trên ESP32-S3/ESP-IDF 6 đã xác nhận response liền 9.976 byte
dừng ở 4.320 byte rồi `send` trả `EAGAIN`, đúng với triệu chứng webview trắng.
Sau khi persist form thành công, state transition sang station phải chậm tối thiểu
750 ms để JSON response rời socket. Trong flow provisioning tương tác, firmware giữ
SoftAP/DHCP/DNS/HTTP tạm thời ở `APSTA`, cung cấp `GET /api/status` chỉ đọc và chỉ báo
`connected` sau `IP_EVENT_STA_GOT_IP`. Portal đóng sau khi browser quan sát success
với grace 3 giây hoặc hard fallback 15 giây; cleanup được post về application task,
không đổi netif trong Wi-Fi/HTTP/timer callback. Status không chứa password, SSID,
IP, bootstrap URL, token, challenge hoặc activation code. Boot/reconnect bình thường
vẫn dùng station-only lifecycle.

Không lưu password plain text vào log. Profile record có version, bound cố định,
CRC, reject duplicate SSID và eviction profile ít gần đây nhất khi đủ 5 mạng. NVS
namespace có version/migration và nút factory reset riêng.

## 5. Bootstrap và activation 6 số

### Request

`POST /veetee/ota/` với:

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
    "etag": "cfg1-<43-char-sha256-base64url>",
    "url": "http://192.168.1.20:8001/veetee/config/v1/devices/AA-BB"
  },
  "resources": {
    "version": "1.4.0",
    "manifest_url": "http://192.168.1.20:8001/veetee/artifacts/manifests/01JRESOURCE"
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

Nếu authenticated bootstrap trả `401`, `403` hoặc `404`, firmware coi device
identity đã bị Manager thu hồi/mất dữ liệu và vào `PAIRING_RECOVERY`. Remote response
không được tự xóa token, Wi-Fi hay bootstrap URL: người dùng phải giữ nút vật lý 5
giây để xác nhận recovery. Sau xác nhận, firmware xóa identity và provisioning,
mở AP, lấy code 6 số mới rồi bind lại. `esp_http_client` có thể trả
`ESP_ERR_NOT_SUPPORTED` khi nhận HTTP 401 không có auth challenge hỗ trợ; firmware
vẫn phải đọc status code đã nhận trước khi phân loại lỗi.

MVP có thể tương thích code 6 số hiện tại. Production phải thêm `expires_at`, attempt counter, CSPRNG, HTTPS bắt buộc và signature/challenge proof.

### Signed executable OTA lifecycle

Firmware OTA dùng journal NVS riêng có version + CRC và attempt ID đơn điệu. Worker
chỉ tải từ byte 0 vào inactive `ota_*`, streaming SHA-256, verify image rồi persist
phase `staged`; worker không đổi boot partition và không tự reboot. Application task
phải persist report `rebooting`, gọi commit boot partition, rồi mới `esp_restart()`.
Nếu report hoặc commit lỗi, boot partition được trả về image đang chạy và attempt kết
thúc `failed` theo đường replay bền vững.

Sau reboot, target mới ở `PENDING_VERIFY` chuyển journal sang `pending_health` và
bootstrap vẫn lấy config/resource/UI nhưng defer mọi firmware target mới. Cửa sổ
health có hai giới hạn độc lập: 30 giây bắt đầu từ `GOT_IP`, và overall bound tính từ
boot bằng Wi-Fi connect timeout 60 giây + 30 giây bootstrap grace. Chỉ mark-valid khi
authenticated bootstrap hoàn tất, app về `idle`, audio/wake/UI đều healthy. Health
fail ghi `rollback_requested` trước khi gọi bootloader rollback; chỉ image cũ sau khi
boot thật mới report `rolled_back`. Report `rebooting -> pending_health -> terminal`
được replay đúng thứ tự; attempt chỉ clear sau khi terminal report đã persist bền.

Executable OTA V1 chưa hỗ trợ HTTP Range/resume hoặc download journal theo offset:
retry tải lại image từ byte 0. Resource/UI reconciler có transaction và resume riêng,
không được dùng capability đó để mô tả quá khả năng executable updater.

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
    "examples": ["Hey VeeTee"],
    "pronunciation_hints": {"vi-VN": ["hây vi ti"]},
    "model_pack_artifact_id": "model:esp-sr-hey-veetee:1.0.0",
    "detector_id": "wakenet:hey_veetee",
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

Object trên là metadata quản trị. Snapshot đã ký gửi xuống firmware không dùng
phrase hoặc logical alias như `wakenet:...`: `activation.model_id` và optional
`interrupt.model_id` phải là exact WakeNet model ID mà `srmodels.bin` đang cung cấp,
ví dụ `wn9s_hiesp`. `interrupt` được phép là `null`; nếu có thì phải dùng model ID
khác activation. Firmware và Manager đều từ chối profile dùng cùng một model cho hai
vai trò để tránh đổi role nhưng detector thực tế không đổi.

Activation/interrupt phrase và exit phrase không được xử lý bằng một chuỗi `if/else` hard-code trong firmware. Local detector dùng signed model/config profile vì phải hoạt động khi chưa mở cloud session hoặc cần abort tức thời. Exit intent và các interrupt diễn đạt tự do do ASR + intent model/LLM trên server suy luận theo ngữ cảnh khi audio path cho phép. Các câu trong profile chỉ là examples/training/config data, không phải toàn bộ ngôn ngữ được hỗ trợ.

Runtime wake V1 trên ESP32-S3 là ESP-SR: WakeNet cho activation hoặc MultiNet cho command/interrupt tùy model pack đã build. Firmware load một `srmodels.bin` tương thích resource ABI; `detector_id` chỉ chọn detector/command đã tồn tại trong model pack, không tải native operator tùy ý. Runtime khác như `sherpa-onnx` chỉ được thêm bằng firmware OTA và ADR sau khi benchmark CPU/RAM/latency trên board thật.

ESP-SR `2.4.7` không cung cấp sẵn model tiếng Việt đã được Veetee xác nhận. WakeNet hiện có model/tài liệu built-in hoặc custom cho một số ngôn ngữ như Trung, Anh, Nhật, Pháp; MultiNet command chủ yếu Trung/Anh. Vì `Hey VeeTee` dùng cách đọc mục tiêu của người Việt, production chỉ được bật sau khi model riêng pass corpus giọng Việt, nhiễu/media và near-confusion. Nếu pipeline custom ESP-SR không đạt gate, phải thêm runtime KWS khác bằng signed firmware OTA; không đổi UI text thành model giả và không quảng bá hỗ trợ trước benchmark.

Firmware bring-up hiện dùng model WakeNet9s built-in `Hi ESP` sau cờ `VEETEE_ESP_SR_BRINGUP`. Đây chỉ là profile kỹ thuật để kiểm tra I2S fan-out, queue, task inference, event và cancellation trên board. Model được pack vào `resource_0` khi flash dev; đường production vẫn phải verify manifest/hash/signature, stage inactive resource slot và rollback trước khi chọn active slot.

Mic chỉ có một I2S RX reader. Mỗi frame PCM16 20 ms được gửi không-blocking vào queue detector hữu hạn; task WakeNet riêng drop frame cũ khi backpressure. Mỗi lần đổi detector role tăng generation, nên frame/kết quả của role cũ không thể đánh thức hoặc abort session mới. Detector activation chỉ chạy ở `idle/closing`; detector interrupt chỉ chạy ở `evaluating/thinking/speaking` khi có profile interrupt riêng đã validate. Button luôn hoạt động kể cả model lỗi.

Wake audio là privacy opt-in trong chính snapshot đã ký. Mặc định
`send_wake_audio=false`: firmware không cấp phát cache và không encode/upload audio ở
standby. Khi snapshot hợp lệ đặt `true`, capture task mới cấp một ring PSRAM cố định 32
gói Opus 60 ms (1,92 giây, tối đa khoảng 48 KiB), chỉ ghi khi activation detector đang
hoạt động ở `idle/closing` và overwrite gói cũ nhất khi đầy. Detector generation đổi,
button wake, abort, close hoặc loss không retryable đều làm audio cũ không thể đi vào
phiên mới. Lỗi transport trước khi gửi opening sequence có thể retry đúng cùng
generation/source và snapshot. Ngay khi sequence đã bắt đầu, phần cache còn lại bị
xóa và không được replay trên retry. Config reload/rollback chuyển cờ về `false` phải
dừng record, invalidate generation, xóa cache và giải phóng ring PSRAM trước khi reload
wake runtime; privacy opt-out vẫn fail-closed nếu model/resource reload thất bại. Ngay
khi opt-out đã verify, firmware đồng thời commit một privacy-revocation latch vào byte
reserved của `DeviceConfigRecord` V2; layout vẫn 348 byte và record version vẫn là 2 để
tương thích blob V2 hiện có. Khi boot, trạng thái effective là
`record.revoked || !applied.send_wake_audio`, nên reload lỗi, config bị supersede hoặc
mất điện không thể bật lại cache từ applied config cũ. Chỉ opt-in ở version mới đã qua
health và được `SaveApplied()` commit thành công mới xóa latch trong cùng record CRC.
Trong health window, cả direct config và config đi cùng resource đều nạp detector bằng
runtime copy có `send_wake_audio=false`; firmware chỉ cấp ring và bắt đầu record sau
commit. Nếu bước bật sau commit lỗi, firmware đóng/persist lại latch và report config
`failed` thay vì để opt-in ở trạng thái runtime mơ hồ. Kết quả resource
`already_active` hoặc config `already_applied` về sau không được nâng report thành
`active` khi latch RAM/NVS còn đóng hoặc trạng thái pre-roll thực không khớp config.

Sau server hello, activation wake phát đúng thứ tự
`listen:detect(source=wake_word)` -> 0..32 binary Opus cũ nhất tới mới nhất ->
`listen:start(mode=auto,source=wake_word)`. Event detect không cần chứa phrase; firmware
không hard-code text semantic. Button vẫn chỉ gửi `listen:start(source=button)` và xóa
cache. Reconnect trước khi bắt đầu sequence có thể giữ snapshot cùng generation; nếu đã
gửi dở thì cache bị xóa và raw Opus không được resume trên session V1 mới.

Button interrupt là guarantee của V1. Interrupt bằng giọng nói khi loa đang phát chỉ là best-effort cho tới khi audio path có far-end playback reference và AEC benchmark pass; firmware/UI không được quảng bá full-duplex trước gate này.

Firmware có một đường benchmark AEC cô lập sau
`CONFIG_VEETEE_EXPERIMENTAL_AEC` và cờ này mặc định tắt. Khi bật, PCM mono 24 kHz
đã áp đúng software volume trước khi ghi MAX98357A được hạ mẫu 3:2, đưa qua ring
reference PSRAM 240 ms rồi ghép với mic 16 kHz bằng direct ESP-SR 2.4.7
`aec_process` (`AEC_MODE_SR_LOW_COST`, filter length 4). Output chỉ thay frame của
local detector trong lúc playback thực sự active; conversation capture vẫn chỉ mở
ở `LISTENING`, hello V1 vẫn báo `aec=false` và mode không tự chuyển `realtime`.
Build experimental chỉ là công cụ đo, chưa phải capability sản phẩm. Chỉ đổi ba
gate trên sau khi board thật pass ERLE, false activation/interrupt với tiếng
Việt/media/noise, near-end preservation và interrupt latency p95 dưới 250 ms.

## 7. Input admission và conversation timeout

Firmware chịu trách nhiệm thu audio sạch nhất có thể và gửi metadata signal; server chịu trách nhiệm quyết định input có tạo AI turn hay không. Firmware không hard-code loại nguồn âm thanh cụ thể.

Metadata có thể gồm VAD probability, RMS/noise floor, clipping, frame loss và optional AEC state. Server trả state/telemetry tổng quát như `non_actionable`, `unclear`, `accepted` để UI hiển thị; firmware không cần biết input đến từ TV, quạt hay nguồn cụ thể nào.

Sau mỗi response, firmware giữ assistant gate mở và chạy inactivity watchdog đồng bộ
với server. Mặc định `first_input_timeout` và `between_turns_timeout` đều là 180 giây;
`max_session_seconds=0` và `total_turn_seconds=0` nghĩa là không có absolute/parent
ceiling. VAD/endpoint detection vẫn chốt từng câu trước khi gửi ASR, không chờ
inactivity timer. Khi server gửi sleep/close:

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

### Signed device-config snapshot V1

Manager dựng một projection riêng cho firmware từ `DeviceDesiredState.version`;
version này độc lập với `agentConfigVersion` dùng cho Voice/memory/MCP. Snapshot tối
đa 8 KiB, chỉ dùng số nguyên và restricted JCS, rồi ký detached Ed25519. ETag là
`cfg1-<sha256-base64url-của-canonical-body>` và bao phủ cả chữ ký:

```json
{
  "schema_version": 1,
  "device_id": "d74f1594-765c-4bd5-b07b-6443273777ed",
  "version": 8,
  "wake_profile": {
    "id": "b80f676e-2fd2-47f9-8160-e68cf7e3a260",
    "version": 4,
    "required_resource_version": "1.2.0",
    "activation": {
      "model_id": "wn9s_hiesp",
      "threshold_ppm": 640000,
      "cooldown_ms": 1200
    },
    "interrupt": null,
    "send_wake_audio": false
  },
  "signature": {
    "algorithm": "ed25519",
    "key_id": "veetee-dev-release-2026-01",
    "security_epoch": 1,
    "value": "<base64>"
  }
}
```

Firmware chỉ GET canonical config URL cùng bootstrap origin, không follow redirect,
không nhận compressed response và chỉ gửi Bearer token tới route đó. Nó chỉ gửi ETag
đã apply trong `If-None-Match`; `304` chỉ hợp lệ khi desired version và ETag cùng
khớp record applied. Parser kiểm exact property set, device/version/key/security
epoch, exact WakeNet ID và signature trước khi stage. `send_wake_audio=true` chỉ được
chấp nhận từ body đã ký và chỉ bật ring PSRAM bounded nói trên; sửa cờ mà không ký lại
bị từ chối như mọi tamper config khác.

Record config trong NVS có schema/version/CRC, applied ETag và security-epoch floor.
Firmware probe type/length trước khi đọc record. Blob từ schema prototype hoặc layout
không tương thích chỉ được thay atomically bằng default record tại
`veetee_config/state`; firmware không erase NVS partition/namespace `veetee`, nên Wi-Fi,
activation và device identity vẫn còn để authenticated bootstrap tải lại signed config.
Migration khởi tạo default record trực tiếp tại persistent object, không tạo thêm object
348 byte trên stack. ESP-IDF main task chỉ dùng lúc boot được pin stack 8 KiB và tự xóa
sau khi `app_main` return, nên headroom này không làm tăng task memory steady-state.
Config chỉ apply ở `idle`; nếu yêu cầu resource mới thì resource và config được giữ
trong cùng health transaction. Config-only cũng phải qua health window 5 giây và chỉ
confirm/persist khi quay lại idle với gate đóng. Config mới đến trong health window
không overwrite snapshot đang kiểm tra; target cũ bị supersede phải rollback thay vì
trở thành LKG. Load/persist/health fail restore resource trước đó và last-known-good
config, trong khi button wake vẫn hoạt động. Record được snapshot dưới mutex giữa
reconciler/app task; nâng minimum security epoch sẽ invalidate riêng config cũ và
boot button-only, không xóa Wi-Fi/identity hoặc tạo reboot-loop.

### Trạng thái triển khai resource reconcile

Firmware hiện đã có đường resource A/B hoàn chỉnh ở mức implementation:

- authenticated bootstrap parse và validate `config.url`/ETag cùng
  `resources.version`/`manifest_url`;
- lane wake-resource của shared maintenance executor kéo manifest tối đa 32 KiB bằng
  Bearer device token, không follow redirect và ưu tiên cấp phát response buffer từ
  PSRAM;
- verifier kiểm tra strict schema V1, target/flash/PSRAM/slot, firmware SemVer,
  resource ABI, đúng một ESP-SR member 16 kHz cùng signed detector inventory
  (một activation, optional interrupt, exact WakeNet ID duy nhất), runtime member
  được firmware hỗ trợ, SHA-256 metadata,
  `security_epoch`, trusted `key_id` và detached Ed25519 signature;
- generation cancellation làm target mới nhất thắng và callback chỉ post kết quả
  về application queue;
- payload GET gửi Bearer token + `Device-Id`, yêu cầu identity encoding, hỗ trợ
  `Range: bytes=N-`, kiểm tra `200/206`, `Content-Length` và `Content-Range`;
- resume re-hash prefix đã ghi, xóa lại tail từ checkpoint 256 KiB rồi stream qua
  buffer 8 KiB vào inactive slot; active slot không bị ghi;
- journal NVS versioned + CRC giữ phase/download progress/slot/version/hash và
  security epoch floor, nên mất điện hoặc target mới không làm mất resource đang chạy;
- SHA-256 toàn payload phải khớp trước khi stage; ESP-SR reload chỉ chạy sau delay
  ưu tiên button/wake và khi state machine đang `idle`;
- WakeNet hot-reload dừng và thu hồi task cũ trước khi destroy model; không gọi
  `esp_wn_iface_t::clean()` trong lifecycle vì ESP-SR 2.4.7 đã được đo panic trong
  `model_clean`. PCM queue, model chunk và detector task stack nằm trong PSRAM để
  Wi-Fi/TLS không làm reload thất bại do phân mảnh RAM nội;
- capture callback đi qua close/drain in-flight gate trước khi hot-reload xóa PCM
  queue/model; config wake mới được phép reload từ active resource slot ngay cả khi
  runtime trước đó đang button-only;
- activation chuyển qua `pending_health`, health window xác nhận slot mới; load/task
  health fail hoặc boot active fail sẽ reload previous slot và rollback. Nếu cả hai
  model lỗi, firmware tiếp tục button-only thay vì bootloop;
- lane reporter của cùng executor gửi
  `checking/downloading/verifying/staged/applying/active` hoặc
  `failed/rolled_back` tới Manager API. Intermediate state được coalesce theo
  latest-wins; terminal state đi FIFO và terminal đang retry được giữ trong NVS;
- reported-state sequence tăng đơn điệu, lưu bằng record V1 + CRC độc lập với
  resource recovery journal. Mất HTTP response retry cùng version nên server xử lý
  idempotent; reboot không tái sử dụng version cũ.

`DeviceSettings` (identity/token/bootstrap/WebSocket/config version) có mutex và API
snapshot. Bootstrap poll/NVS writer, reporter, WebSocket, resource/config/firmware
downloader và Wi-Fi không đọc/copy trực tiếp object đang bị mutation, tránh token/URL
bị rỗng hoặc xé trong poll 60 giây.

Phần còn thiếu của lát resource là power-loss/corrupt-payload matrix đầy đủ trên
board thật và UI drift/apply timeline trên Manager Web.

Hardware trace ngày 2026-07-22 đã xác nhận `resource_1` tải, verify, stage, hot-reload
và qua health window thành `active` trên ESP32-S3 N16R8 mà không panic/reboot trong
soak ngắn. Kết quả này chưa thay thế matrix mất điện và soak 10 phút của release gate.

Luồng reconcile:

1. Nhận optional `config_changed` invalidation hoặc authenticated bootstrap poll mỗi
   60 giây khi idle để bù invalidation bị mất.
2. Pull config/manifest bằng HTTP(S) với ETag; không nhận binary qua WebSocket.
3. Validate device target, schema, safe bounds, size, runtime ABI, hash và signature.
4. Download bundle vào inactive resource slot; active slot không bị overwrite.
5. Verify toàn bộ, load smoke test và stage.
6. Apply ở standby/session boundary; wake/button luôn có priority.
7. Report desired/reported state và health window.
8. Rollback slot/profile cũ nếu load/crash/watchdog/detector health fail.

Config report dùng cùng sequence đơn điệu với artifact report và nằm dưới
`state.config` (`desiredVersion`, `appliedVersion`, `phase`, optional `errorCode`).
Manager shallow-merge từng subsystem nên config/resource/UI/firmware report không
xóa trạng thái của nhau. Giữ nút vật lý để vào pairing recovery sẽ cancel cả
reconciler và reset record config cùng identity/provisioning đã được xác nhận.

Resource V1 hiện là single-member raw `srmodels.bin` ở offset 0 để tương thích trực
tiếp `esp_srmodel_init(partition_label)`; signed manifest bên ngoài là index/hash.
Container nhiều member dành cho resource ABI V2. Resource chỉ chứa model/assets/data;
code native, model operator hoặc runtime mới phải cập nhật bằng signed firmware OTA.
NVS không dùng để chứa binary model lớn.

Partition V1 đã freeze cho prototype N16R8 với executable A/B, wake-model A/B và
UI Pack A/B độc lập. `resource_0/resource_1` và `ui_0/ui_1` đều có kích thước 2 MiB;
hai loại artifact có journal, active pointer và rollback riêng nên đổi giao diện
không chiếm hoặc làm rollback wake model. Manager và firmware đều từ chối payload
vượt inactive slot. Nếu một artifact vượt 2 MiB sau khi chốt scope, phải mở ADR đổi
layout hoặc giảm scope; không tự ghi đè slot đang active.

## 9. Audio/realtime defaults

- Mic PCM: 16 kHz, mono, signed 16-bit.
- Speaker decode: 24 kHz, mono.
- Sau khi I2S TX khởi tạo, V1 giữ BCLK/LRCLK hoạt động bằng cách feed zero PCM khi
  playback idle. Đây là mitigation cho pop/chirp của MAX98357A khi clock liên tục
  stop/start; không phát semantic audio và có thể tắt bằng board config nếu revision
  phần cứng có chân `SD/MUTE` điều khiển đúng. Đổi lại là idle power cao hơn.
- Startup chime chỉ phát một lần ở normal/power/USB/JTAG reset, không phát lại trong
  bootstrap hoặc Wi-Fi retry. Reboot loop phải được chẩn đoán bằng reset reason/log,
  không được che bằng cách bỏ chime.
- Compatibility frame: Opus 60 ms.
- Low-latency profile sau khi có fixture: 20 ms hoặc 40 ms, vẫn dùng field `frame_duration` trong hello.
- Chỉ một capture task được đọc I2S RX. Diagnostics dùng cùng task; không tạo một
  reader thứ hai tranh DMA với production capture.
- Capture PCM -> Opus chỉ mở ở `LISTENING`. Trước khi AEC pass, capture hội thoại
  dừng trong `evaluating/thinking/speaking`; button/local interrupt detector vẫn là
  đường abort ưu tiên.
- `CONFIG_VEETEE_EXPERIMENTAL_AEC` mặc định `n`. Nếu bật để benchmark, far-end
  reference phải lấy từ đúng decoded/local-tone PCM sau volume và cùng timeline
  I2S, không dùng text/TTS source hoặc một signal tái tạo. Build này vẫn không mở
  uplink khi speaking và không quảng bá full-duplex.
- Uplink queue bounded và ưu tiên control: khi đầy, drop frame mic cũ nhất để giữ
  realtime. Downlink queue overflow làm session fail rõ ràng thay vì phát stream đã
  mất packet.
- Abort, stop và close đi qua một urgent control queue riêng, được kiểm tra trước
  audio/control thường. Nếu cả urgent lẫn normal queue không nhận lệnh, firmware
  invalidate generation, đóng socket abortive và đi qua `transport_lost`; không phát
  `abort_complete` giả trên session cũ.
- Task điều phối WebSocket của Veetee dùng stack PSRAM riêng, nhưng task I/O của
  `esp_websocket_client` phải giữ stack trong RAM nội vì gọi Wi-Fi/TLS. Budget task
  I/O là 10 KiB: trace board ngày 2026-07-23 sau 12 lượt hội thoại còn tối thiểu
  3.392 byte stack. Budget 12 KiB cũ không tạo được khi heap còn 37.735 byte nhưng
  block nội lớn nhất chỉ 11.776 byte. Không tăng lại budget hoặc chuyển stack I/O
  sang PSRAM nếu chưa stress cả WS/WSS, cache-disabled path và heap fragmentation.
- Trước khi tạo các task maintenance/application, transport giữ một block RAM nội
  liên tục 16 KiB dành cho lần mở WebSocket; nếu heap chỉ còn một block từ 10 KiB tới
  dưới 16 KiB thì fallback reserve đúng 10 KiB. Reserve được giữ trong lúc
  `esp_websocket_client_init`, nhả ngay trước `esp_websocket_client_start`, rồi
  preflight yêu cầu largest internal block ít nhất 10 KiB. Teardown/failure phải lấy
  reserve lại để lần mở sau không phụ thuộc heap đã phân mảnh. Đây là admission floor;
  release gate chặt hơn yêu cầu stats-off soak giữ largest internal block tối thiểu
  16 KiB và hướng tới 20 KiB.
- Khi Wi-Fi/link đã mất, firmware dùng abortive WebSocket stop; không gửi close frame
  lịch sự trên socket chết vì API component có đường chờ không giới hạn. Sau station
  reconnect, lần mở assistant tiếp theo phải tạo session/task mới thay vì kẹt
  `connecting`.
- Khi Wi-Fi vẫn còn nhưng voice socket lỗi transport retryable, firmware tự thử tối
  đa 3 lần với exponential backoff `250/500/1000 ms`, jitter tối đa `125 ms` và hard
  cap `2000 ms`. Mỗi lần retry tạo WebSocket/hello/session V1 mới, không resume raw
  Opus hoặc turn cũ. Protocol/config failure, explicit close, Wi-Fi loss, button
  cancel và long press không retry; hết budget thì đóng gate và về `idle`.
- `tts:start`, binary Opus và `tts:stop` phải được xử lý theo đúng thứ tự. Firmware
  chỉ báo `tts stopped` cho state machine sau khi playback queue drain, không phải
  ngay khi nhận JSON `tts:stop`. Nếu queue đã đầy đến mức không thể xếp marker kết
  thúc, audio task phải hủy generation chưa hoàn chỉnh rồi vẫn phát completion từ
  chính audio task; không được để state kẹt vĩnh viễn ở `SPEAKING`.
- `abort` phải idempotent và hoàn tất trong mục tiêu <100 ms ở local device.
- Khi local abort, firmware đặt `accept_tts_audio=false`, clear decoder/playback queue và chỉ nhận binary TTS lại sau `tts:start` của generation mới. Raw Opus V1 không mang `turn_id`, nên quy tắc này là bắt buộc để frame cũ đang nằm trong socket không phát lại.
- Lỗi ASR/semantic/LLM/TTS được server map vào recovery signal V1 bounded. Firmware
  hủy output cũ, quay lại `LISTENING` nếu gate còn mở và phát một earcon local ngắn;
  recovery không phụ thuộc TTS provider và không chứa câu semantic hard-code.

## 10. Definition of Done firmware V1

- Boot lần đầu vào AP và lưu Wi-Fi thành công.
- Wi-Fi sai/không có router tự quay lại AP.
- OTA bootstrap nhận websocket URL/token và mã 6 số.
- Bind trên manager xong thì firmware activate và reconnect.
- Identity cũ bị Manager từ chối phải vào pairing recovery hữu hạn; chỉ giữ nút vật
  lý 5 giây mới xóa identity/provisioning và mở AP.
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
- State UI render bất đồng bộ, đúng orientation/visibility trên board và không làm
  tăng abort latency; MAX98357A idle không pop/chirp liên tục sau mitigation clock.
- Mobile luôn có trong executable dưới stable ID `signal` làm default/failsafe;
  UI Pack chỉ đổi presentation qua `ui_0/ui_1`, smoke-render trước activate và
  rollback độc lập wake model.
- MCP `initialize/tools/list/tools/call` pass với ít nhất 3 device tools.
- OTA signed artifact update và rollback test pass trên board thật.
