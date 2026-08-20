# Quy trình phát triển server-first

## Trạng thái quyết định

Đây là quy tắc vận hành đã được người dùng chốt cho giai đoạn phát triển server đầu tiên.
Firmware Veetee chưa được phát triển; firmware và `digital-human` upstream chỉ đóng vai
trò client tham chiếu để phát triển, debug và kiểm thử server Veetee.

Toàn bộ backend, frontend, cơ sở dữ liệu và dịch vụ phụ trợ phải chạy trực tiếp trên máy
local hiện tại. Không dùng Docker, Docker Compose hoặc môi trường container trong giai
đoạn này. Mỗi thành phần phải có lệnh cài đặt, khởi động, dừng và kiểm tra local rõ ràng
khi thành phần đó được triển khai theo yêu cầu sau này.

## Cấu hình thiết bị mục tiêu

| Hạng mục | Giá trị đã chốt |
| --- | --- |
| Chip | ESP32-S3 |
| PSRAM | 8 MB, đã xác minh bằng `esptool` |
| Board build upstream | `bread-compact-wifi-lcd` |
| Màn hình | ST7789 LCD, 240 x 280 |
| Ngoại vi | Màn hình, microphone và loa đã nối đúng pinout của board profile và đã hoạt động với firmware tham khảo |
| Ngôn ngữ mặc định | Tiếng Việt, locale upstream `vi-VN` |
| OTA/config endpoint | Server OTA của Veetee sẽ được phát triển trong phạm vi server |
| Wi-Fi | Dùng credential đã lưu trên thiết bị; phải được bảo toàn qua OTA/flash |

### Kết quả xác minh board từ source

Board phải dùng khi build là `bread-compact-wifi-lcd`. Đối chiếu source upstream tại mốc
đã pin cho thấy:

- `main/boards/bread-compact-wifi-lcd/config.json` đặt target `esp32s3` và tên build
  `bread-compact-wifi-lcd`: phù hợp với chip đã chốt.
- Variant mặc định append `CONFIG_LCD_ST7789_240X320=y`: không đúng màn hình 240 x 280
  của thiết bị.
- `main/Kconfig.projbuild` cho phép chọn `LCD_ST7789_240X280` đối với
  `BOARD_TYPE_BREAD_COMPACT_WIFI_LCD`.
- `main/boards/bread-compact-wifi-lcd/config.h` có nhánh
  `CONFIG_LCD_ST7789_240X280`, đặt kích thước 240 x 280, offset Y là 20, SPI mode 0 và
  dùng driver ST7789.
- Board profile dùng `NoAudioCodecSimplex`, microphone 16 kHz, speaker 24 kHz và các pin
  I2S/LCD cố định trong `config.h`.

Kết luận: `bread-compact-wifi-lcd` là đúng board profile cần dùng, nhưng cấu hình mặc
định của variant không đúng kích thước màn hình. Mọi build cho thiết bị này phải chọn
`CONFIG_LCD_ST7789_240X280=y` thay cho `CONFIG_LCD_ST7789_240X320=y`, đồng thời chọn
`CONFIG_LANGUAGE_VI_VN=y`.

Đối với thiết bị test hiện tại, người dùng đã xác nhận màn hình, microphone và loa được
nối đúng board và cả ba đã hoạt động với firmware tham khảo. `esptool` cũng đã nhận dạng
thiết bị là ESP32-S3 revision 0.2 có PSRAM 8 MB. Sau mỗi lần flash vẫn phải smoke-test
màn hình, backlight, microphone, speaker và nút boot để phát hiện regression.

Với thiết bị khác chưa được xác minh, việc source phù hợp về chip/controller/kích thước
không đủ để kết luận pinout và audio wiring trùng phần cứng thật. Phải đối chiếu firmware
đang chạy hoặc serial log trước lần flash đầu tiên. Nếu pinout không khớp, không sửa
upstream: tạo board Veetee riêng ngoài `references/`.

## Wake word

Thiết bị phải được build sẵn chức năng nhận diện câu đánh thức. Danh sách model thực tế
từ ESP-SR 2.4.7 tại baseline firmware đã pin xác nhận ESP32-S3 hỗ trợ:

| Hạng mục | Giá trị đã xác minh |
| --- | --- |
| Model | `wn9_hiesp` |
| Kconfig model | `CONFIG_SR_WN_WN9_HIESP=y` |
| Engine | `CONFIG_USE_AFE_WAKE_WORD=y` |
| Câu đánh thức | “Hi, ESP” |

AI phải dùng model này khi build thiết bị test và phát audio “Hi, ESP” từ loa máy để tự
kích hoạt luồng kiểm thử. Có thể kiểm tra lại danh sách sau khi ESP-IDF đã resolve managed
components bằng `python3 scripts/build.py --list-wake-words`; không đổi model nếu chưa có
lý do kiểm thử cụ thể và chưa cập nhật tài liệu này.

Lệnh build chuẩn cho thiết bị hiện tại là:

```bash
python3 scripts/build.py bread-compact-wifi-lcd \
  --name bread-compact-wifi-lcd \
  --language vi-VN \
  --wake-word wn9_hiesp \
  --build-options-json '{"display_model":"LCD_ST7789_240X280"}'
```

Sau khi build, phải kiểm tra `sdkconfig` xác nhận model, AFE, locale và LCD đều đúng trước
khi flash. Ghi lại model, câu phát thử và kết quả nhận dạng trong báo cáo kiểm thử.

## Quyền dùng source tham khảo

Hai repo `references/` vẫn là read-only về source và Git history:

- Cấm sửa hoặc format file tracked.
- Cấm commit, checkout, pull, fetch, merge, rebase, reset và push.
- Được chọn Kconfig, build, flash, monitor và run để test server.
- Được tạo generated artifact/runtime state đã được ignore như `sdkconfig`, `build/`,
  log, cache, virtual environment hoặc model test.
- Không stage/commit artifact. Trước và sau test phải xác minh upstream không có thay đổi
  tracked bằng `git status --short`.

Nếu cần thay đổi firmware hoặc `digital-human` để hỗ trợ Veetee, phải port/copy phần cần
thiết sang source Veetee ngoài `references/`; không patch trực tiếp upstream.

## Bảo toàn Wi-Fi và NVS

Thiết bị hiện đã có credential Wi-Fi hợp lệ từ firmware trước. Mọi OTA/flash phải giữ
nguyên NVS và credential này.

Quy tắc bắt buộc:

- Không chạy `erase-flash`, không xóa NVS và không flash image phủ lên partition NVS.
- Ưu tiên OTA hoặc `idf.py flash` theo partition layout đã xác minh; không dùng merged
  full-flash image nếu chưa chứng minh range ghi không đè NVS.
- Không thay partition table nếu thay đổi đó có thể di chuyển/ghi đè NVS mà chưa có kế
  hoạch migration và backup.
- Không đọc, in hoặc đưa Wi-Fi credential vào log/tài liệu.
- Sau flash, reboot và chờ thiết bị tự kết nối mạng đã lưu.

Nếu ESP32 phát access point cấu hình Wi-Fi:

1. Tuyệt đối không tự kết nối máy vào access point đó.
2. Không mở captive portal và không nhập credential mới.
3. Reboot ESP32.
4. Chờ và theo dõi serial log để thiết bị thử lại Wi-Fi đã lưu, vì tín hiệu có thể yếu.
5. Chỉ kết luận credential/NVS hỏng khi có log rõ ràng; không tự khởi tạo lại Wi-Fi.

## Thứ tự chọn client test

### 1. Thiết bị thật đang cắm

Ưu tiên thiết bị thật khi phát hiện serial device phù hợp. AI được phép tự thực hiện:

1. Xác định port và chip mà không erase flash.
2. Build board `bread-compact-wifi-lcd` bằng lệnh chuẩn ở mục Wake word, với LCD 240 x
   280, locale `vi-VN`, OTA URL Veetee và model `wn9_hiesp`; không dùng LCD 240 x 320
   mặc định.
3. Flash theo quy tắc bảo toàn NVS.
4. Chạy serial monitor và server log song song.
5. Chờ thiết bị kết nối server Veetee.
6. Phát audio từ loa máy để kích hoạt wake word và hội thoại.
7. Dùng audio/TTS tiếng Việt có sẵn trên máy; tiếng Anh được phép khi hữu ích cho test.
8. Kiểm tra hello, session, uplink/downlink Opus, STT, LLM/TTS, interrupt, reconnect,
   timeout và cleanup theo phạm vi tính năng.
9. Tự debug bằng log server và serial log, sửa source Veetee rồi lặp lại test.

Không phát audio quá lớn hoặc chạy vòng lặp không giới hạn. Ghi rõ phần nào đã xác minh
trên hardware thật và phần nào mới chỉ quan sát qua log.

### 2. Không có thiết bị thật

Dùng client tham khảo:

`veetee-server/references/xiaozhi-esp32-server/main/digital-human`

AI được phép:

- Cài dependency trong virtual environment cô lập, build và chạy `digital-human`.
- Chuẩn bị model wake word/runtime artifact theo tài liệu upstream mà không sửa file
  tracked; nếu cần config tùy biến lâu dài, tạo config/source Veetee ngoài `references/`.
- Mở Brave đến `http://127.0.0.1:8006/index.html`, cấu hình endpoint OTA/WebSocket của
  server Veetee và thao tác UI để test.
- Tự động hóa trình duyệt hoặc viết test client/fake device ngoài `references/` nếu cách
  đó kiểm tra đúng cùng wire flow tốt hơn.

Fallback phải kiểm tra cùng chuỗi chính như thiết bị thật: OTA/config discovery nếu UI
hỗ trợ, WebSocket hello, audio upload, control JSON, audio response, wake/listen/abort,
reconnect và cleanup. Không được coi test REST/API đơn lẻ là thay thế cho luồng thiết bị.

## Bằng chứng kiểm thử cần lưu

Mỗi công việc server liên quan device flow phải báo cáo tối thiểu:

- Client dùng để test: hardware thật hay `digital-human`/simulator.
- Firmware/client baseline và cấu hình protocol.
- Board, locale, wake-word model/phrase nếu dùng thiết bị thật.
- Endpoint và transport đã test, không bao gồm secret.
- Các bước và luồng đã qua/thất bại.
- Trích log cần thiết đã loại bỏ credential/token.
- Giới hạn chưa xác minh, đặc biệt hardware, Wi-Fi yếu, audio/AEC và OTA rollback.
