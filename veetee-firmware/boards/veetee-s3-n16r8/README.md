# Hardware profile: veetee-s3-n16r8

Trạng thái: **chưa freeze pin map trên board thật**.

Baseline tham chiếu từ Xiaozhi `bread-compact-wifi-lcd`:

| Chức năng | GPIO |
|---|---:|
| INMP441 WS / SCK / SD | 4 / 5 / 6 |
| MAX98357A DIN / BCLK / LRC | 7 / 15 / 16 |
| ST7789 MOSI / SCLK / DC / RST / CS / BL | 47 / 21 / 40 / 45 / 41 / 42 |
| BOOT / LED | 0 / 48 |

Trước khi code board implementation:

- xác nhận đúng module ESP32-S3 N16R8 và schematic;
- xác nhận kích thước/offset/mirror/SPI mode của ST7789;
- xác nhận INMP441 L/R slot;
- test mic noise floor và speaker tone;
- ghi lại mọi thay đổi pin bằng ADR, không sửa âm thầm.
