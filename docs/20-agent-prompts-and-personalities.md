# Agent prompt và thư viện tính cách

## 1. Mục tiêu

Veetee dùng prompt theo agent config, tương tự luồng `agent-base-prompt.txt` của
Xiaozhi, nhưng template được kiểm tra và publish thành snapshot bất biến. Đổi tên,
ngôn ngữ, tính cách hoặc cách trả lời không cần build lại firmware.

Prompt không phải một chương trình. Runtime chỉ thay token đơn giản trong allowlist;
không có Jinja expression, filter, vòng lặp, gọi hàm hoặc truy cập thuộc tính. Template
raw và prompt đã render được giữ tách biệt:

```text
draftConfig.prompt.template
        -> validate token/length/timezone/preset
        -> agent_config_version.prompt (immutable)
        -> voice session render với context/tool catalog hiện tại
```

Session vật lý và Realtime Lab cùng tải `agent_config_version` từ Manager API, vì vậy
không có một prompt engine thứ hai cho Lab.

## 2. Cấu hình prompt V1

```json
{
  "schemaVersion": 1,
  "template": "Bạn là {{agent_name}}. Trả lời bằng {{language}}. {{personality}}",
  "language": "Tiếng Việt tự nhiên",
  "timeZone": "Asia/Bangkok",
  "timeZoneSource": "device",
  "personalityPresetId": "stubborn-reasoned",
  "customPersonality": "Có thể bắt bẻ vui khi ngữ cảnh cho phép.",
  "responseStyle": "Ngắn, rõ, phù hợp cho TTS.",
  "userAddress": "bạn"
}
```

Khi publish, Manager bổ sung `catalogVersion`, nhãn preset, nội dung preset đã chọn
và `allowedVariables` vào snapshot. Preset được đóng băng trong snapshot để catalog
thay đổi sau đó không làm version cũ đổi giọng.

`persona` vẫn là trường agent riêng cho vai trò/bối cảnh/chuyên môn nhưng là tùy chọn.
Template chỉ dùng phần này nếu có `{{persona}}`; template mặc định có token để người
dùng có thể điền thêm, còn base prompt tự viết có thể không dùng token đó.

Snapshot V1 vẫn giữ các field personality/persona-related dưới dạng chuỗi rỗng để
client cũ không phải đổi cấu trúc. Đây là mở rộng tương thích ngược của V1: tên agent,
`language` và base prompt vẫn bắt buộc; preset, persona, response style, cách xưng hô
và timezone override có thể bỏ trống.

## 3. Allowlist biến

| Biến | Nguồn | Bắt buộc | Ý nghĩa |
| --- | --- | --- | --- |
| `{{agent_name}}` | tên agent của version | Có | Danh tính trợ lý |
| `{{language}}` | operator nhập | Có | Tên ngôn ngữ tự nhiên để AI dùng khi trả lời |
| `{{locale}}` | `defaultLocale` | Không | BCP-47 cho provider/ASR/TTS |
| `{{persona}}` | trường persona của agent | Không | Vai trò, bối cảnh và chuyên môn |
| `{{personality}}` | preset + custom override | Không | Tính cách dạng instruction data |
| `{{response_style}}` | operator nhập | Không | Nhịp, độ dài và hình thức trả lời |
| `{{user_address}}` | operator nhập | Không | Cách xưng hô, có thể để trống |
| `{{interaction_mode}}` | agent config | Không | `auto`, `manual` hoặc `realtime` |
| `{{config_version}}` | immutable snapshot | Không | Version đang dùng trong session |
| `{{current_date}}` | voice runtime | Không | Ngày theo `timeZone` |
| `{{current_time}}` | voice runtime | Không | Giờ theo `timeZone` |
| `{{timezone}}` | agent config | Không | IANA time zone đã validate |
| `{{device_locale}}` | reported device state | Không | Locale thiết bị sau provisioning, fallback về agent locale |
| `{{device_timezone}}` | reported device state | Không | IANA timezone thiết bị, fallback về prompt timezone |
| `{{device_timezone_offset}}` | reported device state/runtime | Không | UTC offset tại thời điểm mở session |
| `{{available_tools}}` | tool broker của session | Không | Catalog bounded, chỉ để AI biết tool đã cấp |

Mọi token lạ, token không đóng, Jinja expression (`{% ... %}`), attribute access,
filter, gọi hàm hoặc thiếu `{{agent_name}}`/`{{language}}` đều làm publish thất bại.
Các token còn lại được phép bỏ trống hoặc không đưa vào base prompt. Runtime kiểm tra
lại snapshot trước khi gửi provider; không đánh giá chuỗi template như code.

## 4. Personality preset

V1 cung cấp các preset dữ liệu có thể chọn trong Manager Web:

- Điềm tĩnh, chu đáo.
- Ấm áp, đồng cảm.
- Hài hước, tinh nghịch.
- Ngang bướng có lý.
- Cãi tay đôi.
- Thẳng như ruột ngựa.
- Hoài nghi, phản biện.
- Nhà khoa học tò mò.
- Súc tích, thực dụng.
- Huấn luyện viên năng lượng.
- Lạnh lùng, tối giản.
- Mentor nghiêm khắc.
- Bạn thân hay trêu.
- Lịch thiệp, chuyên nghiệp.
- Sáng tạo, giàu tưởng tượng.
- Người kể chuyện dịu dàng.

Các preset chỉ định giọng điệu và cách tranh luận trong prompt. “Ngang bướng” không
được phép bỏ qua sự thật, an toàn hay quyền; “cãi tay đôi” chỉ phản biện lập luận,
không công kích người dùng. Policy tool, authorization, privacy và giới hạn phần
cứng vẫn nằm ở deterministic runtime/policy plane.

Operator có thể thêm `customPersonality` để tinh chỉnh preset. Tinh chỉnh không thay
thế safety/tool policy và không tạo `if personality == ...` trong voice-server.

Ngoài thư viện built-in, operator có thể tạo preset dùng lại theo tenant gồm tên,
mô tả ngắn, accent hiển thị và instruction. Preset tùy chỉnh dùng cùng validation
và publish flow với preset mặc định; khi publish, label/instruction tiếp tục được
đóng băng vào immutable snapshot để Realtime Lab và thiết bị nhận đúng một behavior.
Preset built-in không thể xóa. Preset tùy chỉnh chỉ được xóa khi không còn agent
draft nào tham chiếu; API trả `409` để operator đổi preset trước, còn các version đã
publish vẫn giữ nội dung đã đóng băng và không bị mutate.

## 5. Luồng UI và publish

1. Mở `Trợ lý` trong Manager Web.
2. Nhập tên, locale fallback và tên ngôn ngữ trả lời; chọn `Thiết bị` để ưu tiên
   locale/múi giờ firmware báo lại, hoặc `Cố định` nếu cần một fallback IANA.
3. Có thể chọn preset tính cách, thêm giới thiệu trợ lý và tinh chỉnh nếu cần.
4. Sửa base prompt raw; chỉ `{{agent_name}}` và `{{language}}` bắt buộc, phần giới thiệu có thể viết trực tiếp trong prompt.
5. Kiểm tra live preview; tool catalog và giờ thật được render lại khi mở session.
6. Chọn provider/timeout rồi publish version mới.
7. Thiết bị nhận desired config version qua flow hiện có; session đang chạy giữ
   snapshot cũ tới boundary an toàn. Khi publish version mới, mở `Thiết bị` và
   bấm `Cập nhật vN` cho từng robot được gán để đổi desired pointer; Manager
   không coi publish là đã apply và không tự bỏ qua canary/rollout.
8. Realtime Lab bắt buộc chọn agent đã publish và dùng đúng snapshot đó. Handshake
   `lab.hello` trả `prompt.applied`, version, language và personality để operator
   xác nhận snapshot runtime đã được nạp.

`VeeTee` chỉ có một trợ lý mặc định trong UI; API vẫn giữ model nhiều agent để tenant
và rollout không bị khóa cứng.

## 6. Kiểm thử và giới hạn

Host test phải bao phủ token hợp lệ, token lạ, token thiếu/đóng không đủ, expression,
missing required variable, preset không tồn tại, timezone sai, snapshot legacy và
render tool catalog bounded. Hardware không cần test Wi-Fi cho thay đổi prompt; chỉ
cần nghiệm thu trên board rằng config version mới được pull ở session boundary và
giọng trả lời thực tế khớp ngôn ngữ/preset đã chọn.
