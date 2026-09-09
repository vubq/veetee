# Định hướng phát triển VeeTee

- Phát triển server tương thích với firmware ESP32/Xiaozhi nguyên bản. Không yêu cầu sửa source, áp patch hoặc build/flash firmware tùy biến để dùng các tính năng tiêu chuẩn của server.
- Dùng giao thức và hành vi đã có trong firmware. Không bắt buộc client gửi capability mới hoặc hiểu lệnh/trường JSON riêng của VeeTee.
- Khi firmware thiếu khả năng cần thiết, tối ưu phía server và ghi rõ giới hạn hoặc cung cấp chế độ dự phòng; không mặc định chuyển công việc sang sửa firmware.
- Chỉ thay đổi định hướng này khi người dùng yêu cầu rõ ràng. Cấu hình kết nối qua tùy chọn sẵn có của firmware vẫn được phép; không coi server có thể ép đổi listening mode/AEC nếu giao thức không hỗ trợ.
- `references/` dùng để đối chiếu và không được commit vào repo chính. Đọc phiên bản nguyên bản theo commit trong `REFERENCE_BASELINES.md`; nếu working tree reference có thay đổi local thì phải phân biệt chúng với baseline trước khi dùng làm bằng chứng. Không tự sửa/reset source tham khảo nếu user không yêu cầu.
- Phân biệt kiểm thử server/mô phỏng với kiểm thử ESP32 thật. Không kết luận loa đã dừng, AEC hoạt động hoặc thiết bị phát hết câu chỉ từ sự kiện server gửi xong audio.
- Các quyết định ngữ nghĩa từ lời người dùng như intent, chọn tool/function, memory mutation, xác nhận, kết thúc hội thoại và ngôn ngữ phải do AI quyết định từ context và tool schema/prompt. Không triển khai keyword list, exact phrase matcher, regex classifier, whitelist hoặc hardcode mẫu câu để tự route/chọn tool thay AI, trừ khi người dùng yêu cầu rõ một ngoại lệ deterministic. Server chỉ giữ logic deterministic cho protocol/lifecycle, schema và permission validation, ownership, deadline/cancel, execution safety và receipt/state invariants.
