# Hardware profile: veetee-s3-n16r8

Trạng thái: **đã probe đúng ESP32-S3 N16R8; wiring/pin map chưa freeze**.

Probe qua ROM bootloader xác nhận ESP32-S3 revision v0.2, flash 16 MB, PSRAM
8 MB và crystal 40 MHz. Không lưu MAC thật trong repository.

Baseline tham chiếu từ Xiaozhi `bread-compact-wifi-lcd`:

| Chức năng | GPIO |
|---|---:|
| INMP441 WS / SCK / SD | 4 / 5 / 6 |
| MAX98357A DIN / BCLK / LRC | 7 / 15 / 16 |
| ST7789 MOSI / SCLK / DC / RST / CS / BL | 47 / 21 / 40 / 45 / 41 / 42 |
| BOOT / LED | 0 / 48 |

Trước khi freeze board implementation:

- xác nhận đúng module ESP32-S3 N16R8 và schematic;
- xác nhận kích thước/offset/mirror/SPI mode của ST7789;
- xác nhận INMP441 L/R slot;
- test mic noise floor và speaker tone;
- ghi lại mọi thay đổi pin bằng ADR, không sửa âm thầm.

Firmware giữ các biến thể ST7789 width/height/offset/mirror/invert và INMP441
left/right slot trong Kconfig để smoke test không đóng băng một giả định chưa đo.

## Hardware smoke 2026-07-21

Đã build/flash ESP-IDF 6.0.2 qua `/dev/ttyACM0` và xác nhận bằng log:

- boot từ `ota_0`, nhận đúng partition executable A/B + resource A/B;
- octal PSRAM 8 MB chạy 80 MHz và memory test pass;
- ST7789 init 240x320, offset 0/0, SPI 40 MHz và gửi color bars thành công;
- I2S simplex mic 16 kHz left slot + speaker 24 kHz khởi tạo thành công;
- boot tone ghi xong; mic trả 16.000 sample/giây, signal thay đổi và chưa clipping;
- application state machine chạy ổn định; blank settings vào `wifi_configuring`,
  phát AP `Veetee-XXXX`, không watchdog/panic/reset trong cửa sổ monitor ngắn.

Các mục vẫn cần xác nhận bằng mắt/tai/tay trước khi freeze: color bars đúng màu và
orientation, tone nghe sạch, lời nói làm mic level thay đổi, short/long/5-second
button event đúng, LED GPIO 48 có tồn tại trên board và INMP441 L/R thực tế khớp
left slot.
