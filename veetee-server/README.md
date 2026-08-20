# Veetee Server

## Trạng thái hiện tại

`veetee-server` là không gian phát triển server và web console của Veetee. Source đầu
tiên hiện có là bộ UX/UI frontend Vue 3 + Vite responsive trong `web/`; backend, database
schema, deployment manifest và API chính thức chưa được triển khai.

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
|-- server/         # Khung backend Python, chưa triển khai
|-- web/            # Veetee Console Vue 3 + Vite
|-- contracts/      # Khung contract device/web, chưa triển khai
|-- deploy/         # Khung vận hành local, chưa triển khai
`-- references/     # Upstream chỉ dùng để đọc, đối chiếu và thử nghiệm riêng
```

Frontend được triển khai từng bước theo yêu cầu UI. Các phần backend, contract và vận
hành vẫn chỉ là khung thư mục cho đến khi có yêu cầu cụ thể. Không có service nào trong
`references/` được mặc định coi là service của Veetee.

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

4. Đối chiếu source trong `references/xiaozhi-esp32-server` nếu cần xác minh.
5. Trước khi viết code, chốt rõ phạm vi service, dữ liệu, giao thức và cách triển khai
   mà Veetee thực sự cần.

## Nguyên tắc thao tác

- Code mới của Veetee phải nằm ngoài `references/`.
- Không sửa source tracked trong `references/`; được build/run upstream làm test harness.
- Không mặc định copy monorepo hay chạy full stack tham khảo.
- Thay đổi device protocol phải được đối chiếu đồng thời với `../veetee-firmware`.
- API key, token, database password và service secret không được ghi vào source/tài liệu.
- Endpoint, port và provider trong `docs/` là giá trị quan sát, chưa phải contract Veetee.
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

Lệnh frontend local nằm trong [web/README.md](web/README.md). Backend chưa có lệnh
run/build/test. Lệnh upstream chỉ được dùng để chạy test harness theo
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
