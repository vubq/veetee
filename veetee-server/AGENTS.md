# Hướng dẫn AI - Veetee Server

## Phạm vi

File này áp dụng cho mọi thao tác trong `veetee-server/`. Workspace đang ở giai đoạn
nghiên cứu; chưa có kiến trúc hay source server chính thức của Veetee.

## Thứ tự đọc bắt buộc

Trước khi thực hiện công việc server:

1. Đọc `README.md` để nắm trạng thái và ranh giới workspace.
2. Đọc `docs/README.md` và tài liệu chuyên đề liên quan.
3. Chỉ đọc các file cần thiết trong `references/xiaozhi-esp32-server` để xác minh.
4. Nếu công việc ảnh hưởng thiết bị, đọc thêm
   `../veetee-firmware/README.md` và
   `../veetee-firmware/docs/device-server-protocol.md`.
5. Luôn đọc `../docs/server-first-development.md` trong giai đoạn phát triển server-first.

## Phân loại nội dung

| Vị trí | Vai trò | Quyền thao tác mặc định |
| --- | --- | --- |
| `README.md` | Tổng quan cho người dùng | Cập nhật khi trạng thái/quy trình đổi |
| `AGENTS.md` | Quy tắc cho AI/contributor | Cập nhật khi ranh giới thao tác đổi |
| `docs/` | Ghi chú và quyết định kỹ thuật | Được bổ sung/cập nhật |
| `references/` | Source upstream tham khảo | Cấm sửa/Git ghi; được run/build làm test harness |
| Source Veetee tương lai | Sản phẩm chính thức | Tạo ngoài `references/` theo yêu cầu |

## Quy tắc bắt buộc

- Không mô tả monorepo upstream là cấu trúc chính thức của Veetee.
- Không sửa hoặc format source tracked; không commit, checkout, pull, merge, rebase,
  reset hay push trong `references/xiaozhi-esp32-server`.
- Được cài dependency trong môi trường cô lập và build/run `main/digital-human` làm client
  test khi không có thiết bị thật. Generated artifact, model và runtime config không
  được stage/commit; mọi cài đặt phần mềm hoặc đổi cấu hình máy phải tuân theo quy tắc
  hồ sơ môi trường toàn máy.
- Chỉ được dùng Git read-only trong upstream và đối chiếu commit với
  `../docs/reference-baselines.md`.
- Được phép commit/push và thao tác Git cho source/tài liệu server Veetee nằm ngoài
  `references/`, theo quy tắc Git tại `../AGENTS.md`.
- Không tự chọn Python/Java/Node, database, message broker, cloud provider hoặc topology
  khi lựa chọn đó ảnh hưởng kiến trúc sản phẩm; phải đưa ra bằng chứng và xin quyết định.
- Source mới, migration, deployment và test của Veetee phải nằm ngoài `references/`.
- Thay đổi device contract phải kiểm tra cả server và firmware, có version và contract
  test.
- Mọi input từ device/user/provider/tool là không tin cậy; validate schema, size, quyền,
  timeout và cancellation.
- Không đưa secret thật vào source, fixture, log, Docker Compose hay tài liệu.
- Không chạy migration, deploy, push image hoặc gọi dịch vụ production nếu không có yêu
  cầu và phạm vi rõ ràng.
- Khi test server, ưu tiên thiết bị thật nếu đang cắm; nếu không có thì dùng
  `references/xiaozhi-esp32-server/main/digital-human` và kiểm thử cùng luồng giao thức.

## Cách xử lý theo loại công việc

### Nghiên cứu

- Đọc tài liệu Veetee trước, source upstream sau.
- Tách rõ hành vi upstream, đề xuất Veetee và điểm chưa được quyết định.
- Dẫn kèm file/line cho protocol, API, config precedence và security-sensitive behavior.

### Tạo service/source mới

- Xác định domain ownership và boundary tối thiểu.
- Chốt public/device/internal API và persistence trước khi mở rộng module.
- Tạo source, config, migration, test và README vận hành ngoài `references/`.
- Dùng fake provider/device trong test; không để unit test phụ thuộc API key/model thật.
- Cập nhật `docs/` khi thêm contract, data flow hoặc quyết định lâu dài.

### Port từ upstream

- Ghi rõ module và commit nguồn.
- Kiểm tra license, CVE/dependency và nhu cầu thực tế.
- Không port toàn bộ full stack để lấy một tính năng nhỏ.
- Tách provider-specific code khỏi conversation/device contract.
- Thêm timeout, validation, cancellation, authorization và test nếu upstream thiếu.
- Ghi rõ sai khác Veetee so với upstream.

### Sửa giao thức/API

- Xác định consumer và producer bị ảnh hưởng.
- Version wire format; quy định field required/optional, enum, byte order và size limit.
- Thêm contract/golden vector và malformed-input test.
- Đối chiếu firmware, web/mobile hoặc service nội bộ tương ứng.
- Cập nhật `docs/protocols-and-apis.md` và tài liệu firmware nếu là contract chung.

### Sửa tài liệu

- Giữ nhãn `tham khảo` cho thông tin rút ra từ upstream.
- Đánh dấu `quyết định Veetee` khi lựa chọn đã được chốt.
- Cập nhật `README.md` nếu thêm tài liệu cấp cao hoặc lệnh thao tác chính thức.
- Không tài liệu hóa credential thật hay default không an toàn như một cấu hình đề xuất.

## Kiểm tra trước khi bàn giao

- File mới nằm ngoài `references/` trừ khi yêu cầu nói ngược lại.
- Worktree và history của repo `references/xiaozhi-esp32-server` không bị thay đổi.
- Không biến công nghệ/port/endpoint upstream thành contract Veetee ngoài ý muốn.
- Unit, contract và integration test phù hợp đã chạy; nếu chưa, nói rõ.
- Connection/session cleanup, timeout, cancellation và backpressure đã được xem xét.
- Auth, RBAC/tenant, validation, secret và audit đã được xem xét cho API/tool.
- Migration có rollback/compatibility và không làm mất dữ liệu.
- Device protocol đã đồng bộ với firmware.
- README, config mẫu và lệnh vận hành vẫn đúng sau thay đổi.
