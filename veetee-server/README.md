# Veetee Server

## Trạng thái hiện tại

`veetee-server` là không gian phát triển server và web console của Veetee. Backend FastAPI
hiện có nền tảng M1, state machine, Device WebSocket, OTA discovery, audio primitives,
fake AI pipeline deterministic và device simulator; frontend Vue 3 + Vite nằm trong
`web/`. Database, provider AI production và deployment manifest chưa được triển khai.

Source trong `references/xiaozhi-esp32-server` là upstream để nghiên cứu, không phải
backend đang vận hành của Veetee và không áp đặt Python, Java, Vue hay deployment model
cho dự án.

Giai đoạn hiện tại ưu tiên phát triển server. Server được test trước với thiết bị ESP32
thật chạy firmware tham khảo; khi không có thiết bị, dùng `digital-human` làm client thay
thế. Xem [quy trình server-first](../docs/server-first-development.md).

## Cấu trúc

```text
veetee-server/
|-- README.md       # Điểm bắt đầu cho người dùng
|-- AGENTS.md       # Hướng dẫn thao tác cho AI/contributor
|-- docs/           # Ghi chú kỹ thuật rút ra từ source tham khảo
|-- server/         # Backend FastAPI và test M1
|-- web/            # Veetee Console Vue 3 + Vite
|-- contracts/      # Golden vectors device/web
|-- deploy/         # Khung vận hành local, chưa triển khai
`-- references/     # Upstream chỉ dùng để đọc, đối chiếu và thử nghiệm riêng
```

Backend và contract đang được triển khai theo các mốc trong kế hoạch chính thức. Không có
service nào trong `references/` được mặc định coi là service của Veetee.

## Bắt đầu từ đâu

1. Đọc [mục lục tài liệu](docs/README.md).
2. Đọc [tổng quan kiến trúc tham khảo](docs/architecture.md).
3. Chọn tài liệu theo công việc:

| Công việc | Tài liệu nên đọc |
| --- | --- |
| Service boundary, deployment | [Tổng quan kiến trúc](docs/architecture.md) |
| WebSocket, audio session, AI flow | [Realtime AI pipeline](docs/realtime-ai-pipeline.md) |
| Device protocol, HTTP, manager API | [Giao thức và API](docs/protocols-and-apis.md) |
| Model/provider/config | [Provider và cấu hình](docs/providers-and-configuration.md) |
| Auth, vận hành, scale, test | [Bảo mật và kiểm thử](docs/security-operations-testing.md) |
| Thiết bị thật và client thay thế | [Quy trình server-first](../docs/server-first-development.md) |
| Kế hoạch xây server theo mốc | [Kế hoạch triển khai Veetee Server](docs/server-implementation-plan.md) |

4. Đối chiếu source trong `references/xiaozhi-esp32-server` nếu cần xác minh.
5. Trước khi viết code, chốt rõ phạm vi service, dữ liệu, giao thức và cách triển khai
   mà Veetee thực sự cần.

## Nguyên tắc thao tác

- Code mới của Veetee phải nằm ngoài `references/`.
- Không sửa source tracked trong `references/`; được build/run upstream làm test harness.
- Không mặc định copy monorepo hay chạy full stack tham khảo.
- Thay đổi device protocol phải được đối chiếu đồng thời với `../veetee-firmware`.
- API key, token, database password và service secret không được ghi vào source/tài liệu.
- Endpoint, port và provider trong tài liệu khảo sát là giá trị quan sát. Ngoại lệ là
  các mục được đánh dấu quyết định Veetee hoặc quyết định chờ cổng duyệt trong
  [kế hoạch triển khai](docs/server-implementation-plan.md).
- Public contract Veetee không được dùng tên/namespace upstream. Tương thích firmware
  được thực hiện bằng wire behavior và endpoint Veetee do OTA/config discovery trả về.
- Mỗi API/service chính thức cần có ownership, cấu hình, migration, test và cách vận
  hành được tài liệu hóa.

## Quy trình cho một công việc mới

```text
yêu cầu
  -> xác định domain/service bị ảnh hưởng
  -> đọc docs liên quan
  -> đối chiếu upstream nếu cần
  -> chốt contract và dữ liệu Veetee
  -> tạo source ngoài references
  -> test contract/integration/security
  -> cập nhật tài liệu và cách vận hành
```

Lệnh frontend local nằm trong [web/README.md](web/README.md); lệnh backend nằm trong
[server/README.md](server/README.md). Lệnh upstream chỉ được dùng để chạy test harness theo
`../docs/server-first-development.md`, không mặc nhiên trở thành quy trình build/deploy
của Veetee.

## Quan hệ với firmware

Server và firmware cùng sở hữu các contract:

- Device identity, binding và authentication.
- WebSocket/MQTT session và reconnect.
- JSON control message và binary audio frame.
- Opus parameters, AEC timestamp và backpressure.
- MCP capability/tool authorization.
- Activation, OTA discovery, rollout và reporting.

Thay đổi contract chung cần có version, test vector và cập nhật tài liệu ở cả hai mục.
