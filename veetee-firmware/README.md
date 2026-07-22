# veetee-firmware

Source repo firmware của robot Veetee, mục tiêu đầu tiên là ESP32-S3 N16R8 + ST7789 + INMP441 + MAX98357A.

## Scope V1

- ESP-IDF 6.0.2/C++17.
- Một board factory `veetee-s3-n16r8`.
- Wi-Fi station + AP captive portal fallback.
- OTA/bootstrap, activation code 6 số và signed A/B OTA.
- WebSocket V1 theo wire contract đã kiểm chứng, Opus, auto conversation, assistant gate, wake/abort và optional manual/PTT compatibility.
- Hai cách wake: button và ESP-SR activation wake word; interrupt profile chạy khi AI đang xử lý và best-effort khi đang phát tiếng tới khi AEC gate pass.
- Session timeout/closing grace nhận từ signed agent config; firmware tự recover nếu server treo.
- Signed resource bundle cho wake/model/assets dùng inactive slot, verify rồi activate; executable/runtime vẫn chỉ qua A/B firmware OTA.
- Device MCP `initialize`, `tools/list`, `tools/call`.
- Locale mặc định `vi-VN`, fallback `en-US`.

Wake phrase sản phẩm là `Hey VeeTee`, đọc “hây vi ti”. Firmware không so transcript
với phrase này; Web/API lưu profile/model ID và metadata pronunciation để train,
benchmark rồi phân phối model pack đã ký. ESP-SR `2.4.7` chưa có model tiếng Việt
hoặc `Hey VeeTee` built-in đã validate. Build dev hiện dùng `Hi ESP` chỉ để bring-up
đường PCM/task/event trên board và không được dùng làm capability production.

## Source layout mục tiêu

```text
main/
├── app/
├── audio/
├── board/
├── display/
├── mcp/
├── network/
├── ota/
├── config/
├── resources/
├── settings/
└── transport/
boards/veetee-s3-n16r8/
partitions/
tests/
```

## Spec bắt buộc

- `../docs/03-firmware-spec.md`
- `../docs/04-protocol-compatibility.md`
- `../docs/05-realtime-conversation.md`
- `../docs/09-testing-security-operations.md`

Không copy cả `references/xiaozhi-esp32/main`. Chỉ mang code hoặc pattern sau khi xác định owner module, license notice và test tương ứng.

## Build và hardware bring-up

```bash
source /home/vubq/.espressif/v6.0.2/esp-idf/export.sh
cd veetee-firmware
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

Host test cho state machine không phụ thuộc ESP-IDF:

```bash
cmake -S tests -B build/host-tests
cmake --build build/host-tests
ctest --test-dir build/host-tests --output-on-failure
```

Firmware hiện có ST7789 state UI bất đồng bộ cho toàn bộ boot/conversation/recovery
flow, startup chime ngắn và button event qua application queue. Display task dùng
queue depth 1 nên redraw chậm không chặn abort/network/audio hot path. Kích
thước/offset/mirror màn hình và INMP441 slot được đổi bằng `idf.py menuconfig`; độ
sáng/orientation cuối cùng vẫn cần nghiệm thu trực tiếp trên phần cứng.

ESP-SR bring-up được bật bởi `CONFIG_VEETEE_ESP_SR_BRINGUP`. Build tạo
`build/srmodels/srmodels.bin`, kiểm tra không vượt partition và `idf.py flash` ghi
nó vào `resource_0`. Một I2S reader fan-out frame PCM 20 ms vào queue hữu hạn;
WakeNet chạy ở task riêng, drop frame cũ khi nghẽn và generation-check khi đổi
activation/interrupt role. Detector buffers và task stack dùng PSRAM; hot-reload
dừng/thu hồi task cũ rồi destroy model thay vì gọi `clean()` không an toàn của
ESP-SR 2.4.7. Resource updater đã stage/verify/activate slot A/B theo signed manifest
trước khi hot-reload. Board smoke đã xác nhận model load, contract 16 kHz
mono/chunk 512, startup chime, microphone, Wi-Fi, activation và resource apply.
I2S TX giữ clock bằng zero PCM khi idle để tránh MAX98357A pop/chirp do clock
stop/start; đây là mitigation mặc định, còn xác nhận âm thanh thực tế và phương án
hardware `SD/MUTE` thuộc board acceptance.
`resource_1` đã qua health window thành `active` và soak ngắn hơn một phút không
panic/watchdog; soak 10 phút cùng power-loss matrix vẫn là release gate.

Khi chưa có cấu hình, firmware phát AP `Veetee-XXXX` và captive portal tại
`http://192.168.4.1`. Portal mobile-first scan SSID/RSSI, đánh dấu mạng đã lưu,
nhận Wi-Fi, bootstrap URL LAN, locale và wake profile ID; password không được trả
về browser hay ghi log. NVS schema V3 lưu tối đa 5 profile trong record 512 byte có
CRC và tự migrate cặp credential V2. Khi boot, firmware ưu tiên kết hợp
last-success/MRU với RSSI, vẫn thử mạng ẩn, rescan có delay và chỉ mở AP nếu không
mạng nào lấy được IP trong timeout 60 giây. Chọn lại mạng đã lưu với password rỗng
sẽ dùng lại secret cũ. HTTP task dành 12 KiB stack để chịu được iOS captive webview
trên ESP-IDF 6; 4 KiB default đã được xác nhận gây reboot khi gửi portal. Settings
cũng giữ `client_id` UUID bền vững. AP quảng bá DNS nội bộ nhưng không dùng DHCP
option 114 sai mục đích cho trang HTML. Scan chỉ bắt đầu từ request `/api/scan` sau
khi client đã nhận IPv4, tránh channel hopping đúng lúc DHCP và chỉ trả cache sau đó.
DNS/HTTP chỉ được mở sau khi `esp_netif` xác nhận DHCP server đã `STARTED`.
Giống lifecycle Xiaozhi, AP/station netif chỉ tồn tại trong mode tương ứng và được
destroy sau `esp_wifi_stop()` khi chuyển mode. Provisioning giữ static-ARP unicast
DHCP path và profile RX nhỏ của Xiaozhi để dynamic TX còn đủ internal/DMA memory.
Giống flow captive portal của Xiaozhi, các probe Apple/Android/Windows/Firefox
nhận `302 Found` tới `http://192.168.4.1/?_=<monotonic-nonce>`. Redirect có
cache-busting mở thẳng portal trong webview được OS bind với SoftAP, không qua
trang loading trung gian. Mọi response dùng `Connection: close` và timeout 15 giây.
Chỉ probe captive đã biết mới redirect; favicon trả `204` và URL lạ giữ 404 mặc
định để resource phụ không thể reload portal giữa lúc người dùng nhập cấu hình.
Khi client cuối rời AP mà chưa lưu, firmware đóng captive HTTP session cũ, hủy scan
đang chạy và giữ DHCP hoạt động; reconnect không cần reboot ESP32.
HTML/CSS/JavaScript được tách thành resource và gửi theo chunk 1 KiB để tránh lỗi
gửi dừng tại 4.320 byte đã đo trên phần cứng. Sau khi lưu,
firmware giữ AP thêm 750 ms để response hoàn tất trước khi chuyển sang station.
Wi-Fi scan buffers nằm trong manager thay vì system-event stack; event task dùng
4 KiB để tránh reboot loop sau `WIFI_EVENT_SCAN_DONE`.

## Bootstrap và activation hiện tại

- Sau khi nhận IP, firmware POST system report tới bootstrap URL `/veetee/ota/` đã provision.
- Request gửi hardware/client/model/firmware/locale headers; token và challenge
  không được ghi log. HTTP(S) redirect bị từ chối để credential không bị chuyển
  sang host ngoài bootstrap trust.
- URL provisioning chỉ chấp nhận HTTP(S) có host hợp lệ, không userinfo, query hay
  fragment. WebSocket URL trả về chỉ chấp nhận `ws://` hoặc `wss://` cùng policy.
- Code 6 số và challenge được lưu trong activation record có version + CRC, độc
  lập với Wi-Fi profile record của NVS schema V3. Khi activate thành công, cùng
  activation record được thay atomically bằng device ID,
  scoped token, WebSocket URL và config version.
- Ticket pending được refresh ngay sau reboot và mỗi 30 giây; ticket hết TTL được
  server thay bằng code mới thay vì firmware poll challenge cũ vô hạn.
- Authenticated bootstrap bị Manager từ chối sẽ vào `pairing_recovery` thay vì
  retry vô hạn hoặc tự xóa token. Giữ nút vật lý 5 giây mới xóa identity cùng Wi-Fi/
  bootstrap provisioning và mở AP để bind lại bằng code 6 số.
- Mã được vẽ bằng renderer số local, không chứa câu semantic hard-code. Sau bind,
  màn hình xóa code và chuyển sang trạng thái standby.

Authenticated bootstrap cũng nhận optional config/resource desired state. Firmware
đã có task bounded để pull manifest tối đa 32 KiB, từ chối redirect, verify strict
schema/board/flash/PSRAM/slot/SemVer/resource ABI/runtime, security epoch và
detached Ed25519. Sau đó firmware stream raw ESP-SR payload vào inactive resource
slot với HTTP Range resume, SHA-256, CRC-protected NVS journal, safe-boundary hot
reload, health window và rollback. Cancellation giữ active slot cùng checkpoint
256 KiB gần nhất. Verifier dùng Monocypher 4.0.3 và restricted JCS; payload hash
dùng PSA Crypto. Reporter task gửi apply state bằng authenticated `PUT`, persist
sequence/terminal retry trong NVS riêng và coalesce trạng thái trung gian. Hardware
E2E portal -> Wi-Fi -> bootstrap -> code 6 số -> bind -> activate -> signed resource
apply đã pass trên thiết bị hiện tại. Phần resource còn lại là chạy đủ
power-loss/corruption matrix và hiển thị drift/timeline trên web. Voice E2E vật lý
vẫn chờ voice-server/local AI hoàn chỉnh và bài test nói trực tiếp với thiết bị.

## WebSocket handshake hiện tại

- Transport chạy trên task riêng; callback chỉ assemble frame có bound và post
  command, không stop/destroy client từ callback.
- Header dùng Bearer device token, protocol version, hardware MAC `Device-Id` và
  UUID bền vững `Client-Id`; token/session không được log.
- Firmware gửi device hello fixture 16 kHz/mono/Opus 60 ms, chờ server hello tối đa
  10 giây rồi mới gửi `listen:start(mode=auto)` với source `button` hoặc
  `wake_word` qua cùng một code path.
- JSON text fragment được assemble với giới hạn 8 KiB. Hello sai transport,
  session/audio profile hoặc event có `session_id` khác sẽ đóng session.
- Button/interrupt dùng chung đường `abort`; long press gửi `listen:stop` rồi đóng
  channel. Generation mới làm event của connection cũ trở thành stale.
- Binary Opus upload/download, `tts:start/stop` playback gate, generation isolation
  và button abort đã nằm trong transport/audio lifecycle; voice E2E vật lý vẫn cần
  nghiệm thu nói-nghe trực tiếp với local model runtime.
