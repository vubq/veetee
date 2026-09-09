# AI first semantics rules

## Mục tiêu

VeeTee xử lý hội thoại theo hướng AI quyết định ngữ nghĩa. Server cung cấp context, schema, permission và execution runtime; AI quyết định ý định và cách sử dụng capability.

## Quy tắc bắt buộc

- Intent classification phải do model quyết định từ toàn bộ hội thoại.
- Tool calling phải dựa trên tool schema và context hiện tại.
- Không thêm router bằng keyword, exact phrase, regex, danh sách câu mẫu hoặc hardcode câu người dùng.
- Không tạo tool riêng chỉ để thay thế khả năng suy luận của AI.
- Tool deterministic chỉ chịu trách nhiệm protocol, validation, permission, lifecycle và execution safety.
- Tool result phải là nguồn sự thật cho các dữ liệu như thời gian, trạng thái thiết bị, kết quả hành động.
- Không tự suy luận thành công của action trước khi có receipt từ executor.

## Ví dụ

Người dùng nói "bật đèn giúp tôi" không được xử lý bằng matcher chuỗi "bật đèn". AI nhìn tool đang có, context và quyền hiện tại để chọn tool phù hợp.

Người dùng hỏi giờ hiện tại không được lấy giờ trong prompt hoặc biến cấu hình. AI gọi tool thời gian khi cần và dùng receipt trả về.

## Phạm vi áp dụng

Áp dụng cho mọi capability: thiết bị, memory, lịch, thông tin thời gian, tính toán và MCP tools.

