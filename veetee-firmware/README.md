# Veetee Firmware

## Trạng thái hiện tại

`veetee-firmware` là không gian chuẩn bị cho firmware của Veetee. Hiện tại chưa có
source firmware chính thức, board target, build system hay release artifact của Veetee.
Thư mục mới chỉ gồm tài liệu khảo sát và source tham khảo.

Trong giai đoạn server-first, thiết bị thật dùng firmware upstream với board build
`bread-compact-wifi-lcd`, LCD ST7789 240 x 280 và locale `vi-VN`; xem
`../docs/server-first-development.md` trước khi build/flash.

Không coi code trong `references/` là code của Veetee và không mặc định kiến trúc
Veetee sẽ giống upstream.

## Cấu trúc

```text
veetee-firmware/
|-- README.md       # Điểm bắt đầu cho người dùng
|-- AGENTS.md       # Hướng dẫn thao tác cho AI/contributor
|-- docs/           # Ghi chú kỹ thuật rút ra từ source tham khảo
`-- references/     # Upstream chỉ dùng để đọc, đối chiếu và thử nghiệm riêng
```

Khi source Veetee được tạo, cấu trúc source, test, tool và build sẽ được bổ sung theo
quyết định kỹ thuật cụ thể. Không tạo sẵn các thư mục rỗng để áp đặt kiến trúc sớm.

## Bắt đầu từ đâu

1. Đọc [mục lục tài liệu](docs/README.md).
2. Đọc [tổng quan kiến trúc tham khảo](docs/architecture.md).
3. Chọn tài liệu theo công việc:

| Công việc | Tài liệu nên đọc |
| --- | --- |
| Board, lifecycle, task | [Tổng quan kiến trúc](docs/architecture.md) |
| Class, interface, callback | [Giao diện và phương thức](docs/interfaces.md) |
| Microphone, speaker, VAD, AEC | [Âm thanh và trạng thái](docs/audio-and-state.md) |
| Kết nối với server | [Giao thức thiết bị-server](docs/device-server-protocol.md) |
| Provisioning, activation, OTA | [Khởi tạo, OTA và cấu hình](docs/provisioning-ota-config.md) |

4. Đối chiếu source trong `references/xiaozhi-esp32` nếu cần xác minh implementation.
5. Trước khi viết code, xác định rõ yêu cầu Veetee, target chip/board và phạm vi cần
   kế thừa hay viết lại.

## Nguyên tắc thao tác

- Code mới của Veetee phải nằm ngoài `references/`.
- Không sửa source tracked trong `references/`; được build/flash upstream làm client
  tham chiếu theo `../docs/server-first-development.md`.
- Không copy nguyên module upstream nếu chưa đánh giá license, dependency, bộ nhớ và
  khả năng tương thích phần cứng.
- Thay đổi giao thức thiết bị-server phải được đối chiếu đồng thời với
  `../veetee-server`.
- Thông số trong `docs/` là giá trị quan sát từ upstream, không phải yêu cầu sản phẩm.
- Khi có quyết định chính thức, cập nhật README/tài liệu Veetee và ghi rõ phần nào là
  quyết định, phần nào chỉ là tham khảo.

## Quy trình cho một công việc mới

```text
yêu cầu
  -> xác định phạm vi firmware
  -> đọc docs liên quan
  -> đối chiếu implementation upstream nếu cần
  -> đề xuất/chốt quyết định Veetee
  -> tạo source ngoài references
  -> build/test
  -> cập nhật tài liệu
```

Hiện chưa có lệnh build/test của Veetee. Lệnh trong source tham khảo chỉ có giá trị khi
chạy bên trong repo upstream và không được ghi nhận là quy trình build của Veetee.

## Quan hệ với server

Firmware và server dùng chung các contract sau:

- Device identity và authentication.
- Session lifecycle và feature negotiation.
- JSON control messages và binary audio.
- Opus format, sample rate, frame duration và timestamp.
- MCP tool discovery/invocation.
- Activation, endpoint discovery và OTA metadata.

Bất kỳ thay đổi nào ở các điểm này cần cập nhật tài liệu của cả hai mục và kiểm thử hai
đầu của contract.
