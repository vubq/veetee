# Veetee Console

Frontend quản trị Veetee dùng Vue 3, Vite và TypeScript. Đây là một ứng dụng responsive
duy nhất cho mobile và PC; không có bản giao diện riêng theo thiết bị.

## Chạy local

```bash
npm install
npm run dev
```

Mặc định Vite chạy tại `http://127.0.0.1:5173`. Không dùng Docker hoặc Docker Compose.

## Kiểm tra

```bash
npm run type-check
npm run build
npm run test:e2e
```

Suite Playwright chạy Chromium ở desktop 1440 x 900 và mobile 390 x 844. API được mock
ở HTTP boundary để kiểm tra deterministic các trạng thái thành công/lỗi; backend và
PostgreSQL được kiểm riêng trong test suite server. Test fail khi có lỗi Vue/browser,
`console.error` hoặc horizontal overflow trong scenario responsive.

## Phạm vi hiện tại

- Màn hình đăng nhập riêng; bearer token chỉ giữ trong memory. Đăng xuất luôn xóa token
  cục bộ trước, rồi mới gọi máy chủ để thu hồi phiên; nếu lời gọi thu hồi thất bại
  (lỗi mạng hoặc 5xx), console vẫn về màn hình đăng nhập nhưng hiển thị cảnh báo người dùng
  nên đăng nhập lại và thử đăng xuất khi kết nối ổn định.
- App shell, header, footer và trang danh sách trợ lý sau khi xác thực.
- Logo Veetee tự thiết kế dạng mặt robot vuông bo nhẹ, mắt sắc, xanh lục–cyan.
- Bộ component UI dùng chung: button, search, dropdown, select, switch, tab, dialog,
  table, badge, empty state và card.
- Agent create/rename/delete, conversation, device bind/unbind, OTA artifact/release/publish
  và cấu hình agent runtime đều gọi API Veetee thật.
- Điều hướng chia ba nhóm `Trợ lý`, `Vận hành` và `Quản trị`. Nhóm Vận hành gồm provider,
  Knowledge/RAG, hiệu chỉnh/ngữ cảnh, external integration/Device MCP và OTA; nhóm Quản trị
  gồm user, system settings/quota và audit log. Toàn bộ dùng chung một layout responsive,
  bảng rộng cuộn trong card thay vì làm tràn ngang trang trên mobile.
- Knowledge hỗ trợ tải tài liệu `text/plain`/`text/markdown`, xem chunk, thử truy vấn và gắn
  dataset vào trợ lý. Correction hỗ trợ rule `exact`/`phrase`, preview và cấu hình context
  provider theo trợ lý.
- Device MCP bắt buộc đi qua `tools/list` rồi `prepare-call` và hộp thoại xác nhận rõ ràng
  trước `call`. Console giữ đúng `session_id`; confirmation token chỉ tồn tại tạm trong memory,
  không render hoặc persist và được xóa ngay khi xác nhận, hủy hay gặp lỗi.
- Owner luôn xem được provider catalog và quota cá nhân. API quản trị trả `403` được hiển thị
  bằng role gate tại chỗ, không xóa phiên hoặc suy đoán role từ email. Admin có workflow quản
  lý user/reset token một lần, typed settings, quota theo bốn cửa sổ và audit filter theo
  actor/action/resource/time.
- Hộp thoại “Cấu hình trợ lý” tối thiểu chỉ expose các trường đã có hiệu lực realtime trong
  pipeline: vai trò (`role_prompt`), tính cách (`personality`), cách xưng hô (`address_style`),
  ngôn ngữ (`language`), mức độ chi tiết (`detail_level`), phong cách trả lời (`response_style`)
  và mô hình LLM lấy từ provider catalog của máy chủ (`kind=llm`). Copy giao diện nói rõ thay
  đổi áp dụng từ lượt hội thoại tiếp theo và lượt hội thoại đang chạy giữ nguyên cấu hình cũ.
- Các trường chưa hiển thị (giọng nói, bộ nhớ, intent/tool) không được đưa vào UI nhưng vẫn
  được gửi nguyên giá trị hiện có khi PUT thông qua mapping `AgentSummary`. Provider catalog
  tải trong hộp thoại có trạng thái loading/error/thử lại; xung đột phiên bản optimistic
  (HTTP 409) hiển thị lỗi kèm hành động tải lại dữ liệu mới và đóng; request generation chống
  phản hồi stale khi đóng/mở hộp thoại hoặc đổi trợ lý.
- Thiết bị bound resolve snapshot ở ranh giới processing; thay đổi đã lưu chỉ áp dụng từ
  lượt sau. Unbind đóng session thiết bị hiện hành để không tiếp tục dùng binding cũ.
- Control chưa có backend không được hiển thị như chức năng khả dụng; các mục này chỉ xuất
  hiện ở trạng thái disabled với nhãn `Sắp có` (ví dụ menu tạo từ mẫu/nhập cấu hình).
- Playwright mock HTTP boundary cho các workflow M6 đại diện, gồm Knowledge upload/search,
  correction preview, Device MCP prepare/xác nhận/call, role gate `403`, quota và audit; mọi
  scenario chạy ở desktop 1440 x 900 và mobile 390 x 844.

Giao diện tham chiếu bố cục và hành vi quan sát từ `https://xiaozhi.me/console/agents`,
nhưng dùng tài sản nhận diện Veetee riêng và không chứa mã nguồn/tài sản thương hiệu của
trang tham chiếu.
