# Định hướng phát triển VeeTee

- Phát triển server tương thích với firmware ESP32/Xiaozhi nguyên bản. Không yêu cầu sửa source, áp patch hoặc build/flash firmware tùy biến để dùng các tính năng tiêu chuẩn của server.
- Dùng giao thức và hành vi đã có trong firmware. Không bắt buộc client gửi capability mới hoặc hiểu lệnh/trường JSON riêng của VeeTee.
- Khi firmware thiếu khả năng cần thiết, tối ưu phía server và ghi rõ giới hạn hoặc cung cấp chế độ dự phòng; không mặc định chuyển công việc sang sửa firmware.
- Chỉ thay đổi định hướng này khi người dùng yêu cầu rõ ràng. Cấu hình kết nối qua tùy chọn sẵn có của firmware vẫn được phép; không coi server có thể ép đổi listening mode/AEC nếu giao thức không hỗ trợ.
- `references/` dùng để đối chiếu. Đọc phiên bản nguyên bản theo commit trong `REFERENCE_BASELINES.md`; working tree tham khảo hiện có sửa đổi thử nghiệm nên không dùng nó làm bằng chứng cho FW nguyên bản. Không tự reset/xóa các sửa đổi đó.
- Phân biệt kiểm thử server/mô phỏng với kiểm thử ESP32 thật. Không kết luận loa đã dừng, AEC hoạt động hoặc thiết bị phát hết câu chỉ từ sự kiện server gửi xong audio.
