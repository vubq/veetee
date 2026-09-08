# Task Plan Handoff Rules

Thư mục này chỉ dùng cho workflow handoff giữa model lập kế hoạch và model thực thi.

## Ràng buộc phát triển server

- Mọi plan cho ESP32 phải tuân thủ định hướng **không sửa firmware** trong `../AGENTS.md`.
- Ghi rõ baseline FW nguyên bản, giao thức sẵn có và giới hạn cần xử lý phía server. Không đưa việc áp patch/build FW tùy biến thành điều kiện nghiệm thu.
- Plan cũ yêu cầu sửa FW phải được điều chỉnh trước khi thực hiện; chỉ dẫn mới nhất của user được ưu tiên.

## Khi nào được dùng

- Chỉ tạo hoặc cập nhật plan trong thư mục này khi user **yêu cầu rõ ràng** các từ như: `lên plan`, `tạo plan`, `lập kế hoạch`, hoặc yêu cầu tương đương.
- Không tự tạo plan cho task thông thường.
- Không biến mọi yêu cầu thành plan chỉ vì task phức tạp.

## Vai trò model lập kế hoạch

Khi user yêu cầu lên plan:

1. Rà source/repo đủ sâu để plan dựa trên trạng thái thật, không đoán.
2. Tạo một file Markdown mới trong `task-plans/` theo mẫu `_TEMPLATE.md`.
3. Tên file dạng `YYYY-MM-DD-ten-task-ngan-gon.md`.
4. Ghi rõ phạm vi, file dự kiến sửa, thứ tự thực hiện, tiêu chí nghiệm thu, test cần chạy, rủi ro và phần chưa chắc chắn.
5. Không implement code chính nếu user chỉ yêu cầu **lên plan**.
6. Plan phải đủ chi tiết để một model khác có thể thực hiện mà không cần đọc lại toàn bộ cuộc hội thoại.

## Vai trò model thực thi

Khi user yêu cầu thực hiện một plan trong thư mục này:

1. Đọc toàn bộ plan được chỉ định trước khi sửa code.
2. Kiểm tra lại trạng thái repo hiện tại vì source có thể đã thay đổi sau lúc plan được tạo.
3. Thực hiện lần lượt theo plan, nhưng được phép điều chỉnh chi tiết nếu evidence trong source cho thấy plan cũ không còn chính xác.
4. Không bỏ qua bước validation/acceptance criteria đã ghi trong plan.
5. Cập nhật checklist và mục `Execution status` trong chính file plan khi hoàn thành từng phần quan trọng.
6. Nếu hoàn tất, đặt trạng thái plan thành `COMPLETED`; nếu còn việc phụ thuộc thiết bị/người dùng, dùng `PARTIAL` và ghi rõ phần còn lại.

## Quy tắc handoff

- Plan là tài liệu truyền việc, không phải source of truth cao hơn yêu cầu mới nhất của user.
- Yêu cầu mới nhất của user luôn có quyền sửa/ghi đè plan cũ.
- Không thực hiện một plan chỉ vì nó tồn tại trong thư mục. Chỉ thực hiện khi user yêu cầu dùng/thực hiện plan đó hoặc ngữ cảnh hiện tại chỉ rõ plan đang active.
- Không commit/push/deploy chỉ vì plan ghi có bước đó; chỉ làm khi user đã cho phép trong yêu cầu hiện tại hoặc quyền đó đã được cấp rõ ràng trong phiên làm việc.
