# Hướng dẫn AI - Veetee Firmware

## Phạm vi

File này áp dụng cho mọi thao tác trong `veetee-firmware/`. Đây là workspace firmware
Veetee đang ở giai đoạn nghiên cứu; chưa có source chính thức.

## Thứ tự đọc bắt buộc

Trước khi thực hiện công việc firmware:

1. Đọc `README.md` để nắm trạng thái và ranh giới workspace.
2. Đọc `docs/README.md` và tài liệu chuyên đề liên quan.
3. Chỉ đọc các file cần thiết trong `references/xiaozhi-esp32` để xác minh chi tiết.
4. Nếu công việc ảnh hưởng wire protocol, đọc thêm
   `../veetee-server/README.md` và `../veetee-server/docs/protocols-and-apis.md`.
5. Nếu build/flash firmware tham khảo hoặc test thiết bị thật, đọc
   `../docs/server-first-development.md`.

## Phân loại nội dung

| Vị trí | Vai trò | Quyền thao tác mặc định |
| --- | --- | --- |
| `README.md` | Tổng quan cho người dùng | Cập nhật khi trạng thái/quy trình đổi |
| `AGENTS.md` | Quy tắc cho AI/contributor | Cập nhật khi ranh giới thao tác đổi |
| `docs/` | Ghi chú và quyết định kỹ thuật | Được bổ sung/cập nhật |
| `references/` | Source upstream tham khảo | Cấm sửa/Git ghi; được build và flash để test server |
| Source Veetee tương lai | Sản phẩm chính thức | Tạo ngoài `references/` theo yêu cầu |

## Quy tắc bắt buộc

- Không mô tả `references/xiaozhi-esp32` là cấu trúc hay source chính thức của Veetee.
- Không sửa hoặc format source tracked; không commit, checkout, pull, merge, rebase,
  reset hay push trong `references/xiaozhi-esp32`.
- Được chọn Kconfig, build, flash và monitor firmware upstream để test server. Các thay
  đổi generated/runtime như `sdkconfig`, `build/` và artifact phải được upstream ignore,
  không stage/commit và phải được báo cáo khi bàn giao.
- Chỉ được dùng Git read-only trong upstream và đối chiếu commit với
  `../docs/reference-baselines.md`.
- Được phép commit/push và thao tác Git cho source/tài liệu firmware Veetee nằm ngoài
  `references/`, theo quy tắc Git tại `../AGENTS.md`.
- Không tự tạo kiến trúc firmware đầy đủ chỉ từ source tham khảo. Nếu lựa chọn chip,
  board, framework, transport hoặc AEC làm thay đổi hướng sản phẩm, phải nêu rõ và xin
  quyết định.
- Trong giai đoạn server-first, build thiết bị thật bằng board upstream
  `bread-compact-wifi-lcd`, bắt buộc override LCD mặc định 240 x 320 thành
  `CONFIG_LCD_ST7789_240X280=y`, chọn `CONFIG_LANGUAGE_VI_VN=y` và dùng wake-word
  model `wn9_hiesp` với câu đánh thức “Hi, ESP”.
- Mọi source mới phải nằm ngoài `references/` và có ownership rõ ràng.
- Thay đổi protocol phải kiểm tra cả firmware và server, bao gồm backward compatibility,
  version, malformed input và timeout.
- Không coi build thành công là hardware validation. Báo cáo rõ phần nào cần test trên
  board, codec, display hoặc network thật.
- Không đưa secret, token, Wi-Fi credential, key OTA hoặc endpoint nội bộ vào source và
  tài liệu mẫu.
- Không xóa NVS, không erase flash và không thay đổi Wi-Fi credential của thiết bị test.
- Tuyệt đối không tự kết nối vào access point cấu hình Wi-Fi do ESP32 phát. Nếu access
  point xuất hiện, reboot thiết bị và chờ thiết bị kết nối lại mạng đã lưu theo quy trình
  `../docs/server-first-development.md`.

## Cách xử lý theo loại công việc

### Nghiên cứu

- Đọc tài liệu Veetee trước, source upstream sau.
- Trả về kết luận kèm file/line upstream quan trọng.
- Tách rõ ba nhóm: hành vi quan sát, đề xuất cho Veetee, điểm chưa được quyết định.

### Tạo kiến trúc/source mới

- Xác nhận target chip/board và yêu cầu sản phẩm liên quan.
- Chọn thay đổi nhỏ nhất giải quyết đúng yêu cầu.
- Tạo source, build config và test ngoài `references/`.
- Tạo README gần source với lệnh build/flash/test có thể lặp lại.
- Cập nhật `docs/` khi xuất hiện contract hoặc quyết định lâu dài.

### Port từ upstream

- Ghi rõ file/module nguồn và commit tham khảo.
- Kiểm tra license và dependency trước khi copy.
- Chỉ port phần cần thiết; không mang theo board/provider/feature không dùng.
- Đổi tên, abstraction và config theo Veetee khi đã có quyết định, không duy trì vỏ bọc
  tương thích nếu không có nhu cầu cụ thể.
- Thêm test cho hành vi đã port và ghi sai khác với upstream.

### Sửa tài liệu

- Giữ nhãn `tham khảo` cho thông tin rút ra từ upstream.
- Đánh dấu rõ `quyết định Veetee` khi một lựa chọn đã được chốt.
- Cập nhật liên kết từ `README.md` nếu thêm tài liệu cấp cao.
- Đối chiếu tài liệu server nếu sửa giao thức chung.

## Kiểm tra trước khi bàn giao

- File mới nằm đúng phạm vi, không nằm trong `references/` ngoài ý muốn.
- Worktree và history của repo `references/xiaozhi-esp32` không bị thay đổi.
- Không ghi thông số upstream thành yêu cầu Veetee nếu chưa được chốt.
- Build/test phù hợp đã chạy; nếu chưa chạy, nói rõ lý do.
- Thay đổi audio đã xem xét queue, latency, interruption, reconnect và AEC.
- Thay đổi board đã xem xét pin, flash, partition, optional capability và OTA identity.
- Thay đổi protocol đã đồng bộ tài liệu/test server.
- Tài liệu và lệnh thao tác vẫn còn đúng sau thay đổi.
