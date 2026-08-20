# Veetee

## Tổng quan

Veetee là dự án thiết bị trợ lý giọng nói gồm hai phạm vi chính:

| Phạm vi | Trách nhiệm dự kiến | Điểm bắt đầu |
| --- | --- | --- |
| Firmware | Phần cứng, audio, kết nối, giao diện thiết bị và OTA | [veetee-firmware](veetee-firmware/README.md) |
| Server | Phiên thiết bị, xử lý AI, API, quản trị và vận hành | [veetee-server](veetee-server/README.md) |

Hiện tại dự án đang ở giai đoạn nghiên cứu và chuẩn bị. Chưa có source chính thức,
kiến trúc đã chốt, build system, database schema, deployment hay bản phát hành Veetee.
Hai phạm vi mới gồm tài liệu tổng hợp và source upstream để tham khảo.

Giai đoạn triển khai đầu tiên ưu tiên server. Việc phát triển và test server dùng firmware
upstream trên thiết bị thật hoặc `digital-human` làm client tham chiếu theo
[quy trình server-first](docs/server-first-development.md).

## Cấu trúc hiện tại

```text
veetee/
|-- README.md
|-- AGENTS.md
|-- veetee-firmware/
|   |-- README.md
|   |-- AGENTS.md
|   |-- docs/
|   `-- references/
`-- veetee-server/
    |-- README.md
    |-- AGENTS.md
    |-- docs/
    `-- references/
```

| Loại nội dung | Ý nghĩa |
| --- | --- |
| `README.md` | Điểm bắt đầu và hướng dẫn cho người dùng |
| `AGENTS.md` | Ranh giới và quy trình thao tác cho AI/contributor |
| `docs/` | Ghi chú kỹ thuật rút ra từ source tham khảo |
| `references/` | Repo upstream để nghiên cứu và làm test harness; cấm sửa source/Git history |

Không coi source trong `references/` là source Veetee. Cấu trúc source sản phẩm sẽ được
tạo sau khi có yêu cầu và quyết định kỹ thuật cụ thể.

## Cách bắt đầu

1. Đọc tài liệu tổng quan này.
2. Xác định công việc thuộc firmware, server hay contract dùng chung.
3. Đọc README và AGENTS của phạm vi tương ứng.
4. Đọc tài liệu chuyên đề trong `docs/`.
5. Chỉ đối chiếu `references/` khi cần xác minh implementation upstream.
6. Tạo source Veetee ngoài `references/`, kèm test và tài liệu phù hợp.

## Điều hướng theo công việc

| Công việc | Tài liệu bắt đầu |
| --- | --- |
| Board, audio, state, provisioning | [Firmware](veetee-firmware/README.md) |
| WebSocket, AI pipeline, provider, API | [Server](veetee-server/README.md) |
| Giao thức thiết bị-server | [Firmware protocol](veetee-firmware/docs/device-server-protocol.md) và [Server API](veetee-server/docs/protocols-and-apis.md) |
| Activation và OTA | [Firmware OTA](veetee-firmware/docs/provisioning-ota-config.md) và [Server security/operations](veetee-server/docs/security-operations-testing.md) |
| Bảo mật đầu cuối | [Server security](veetee-server/docs/security-operations-testing.md) cùng các tài liệu protocol/OTA firmware |
| Quy trình phát triển và test hiện tại | [Server-first development](docs/server-first-development.md) |

## Contract dùng chung

Firmware và server cùng sở hữu các contract sau:

- Danh tính thiết bị, binding, authentication và credential rotation.
- Feature negotiation, session lifecycle, heartbeat, timeout và reconnect.
- JSON control message và binary audio frame.
- Opus format, sample rate, frame duration, timestamp và backpressure.
- Wake word, listening mode, interruption và AEC.
- MCP capability discovery, tool invocation và authorization.
- Provisioning, activation, endpoint discovery, OTA và rollout reporting.

Thay đổi một contract dùng chung phải:

1. Xác định producer và consumer bị ảnh hưởng.
2. Có version và quy tắc tương thích rõ ràng.
3. Cập nhật tài liệu ở cả firmware và server.
4. Có contract test hoặc golden test vector cho hai đầu.
5. Kiểm tra malformed input, timeout, reconnect và security boundary.

## Nguyên tắc dự án

- Lựa chọn upstream chỉ là dữ liệu tham khảo, không phải quyết định Veetee.
- AI được phép commit, push và thực hiện các thao tác Git cần thiết cho phần Veetee nằm
  ngoài `references/`, với điều kiện kiểm tra đúng thay đổi, branch và remote.
- Cấm sửa code/tài liệu và cấm thao tác Git ghi trong hai repo `references/`.
- Mốc commit của hai upstream được lưu tại
  [reference baselines](docs/reference-baselines.md) để đối chiếu update sau này.
- Không tạo kiến trúc rộng hoặc boilerplate lớn trước khi có nhu cầu cụ thể.
- Source mới cần có ownership, cách build/run/test và tài liệu ngắn gọn.
- Không đưa secret thật vào source, log, fixture, tài liệu hay cấu hình mẫu.
- Mỗi quyết định chính thức cần được phân biệt với ghi chú khảo sát upstream.
- Build phần mềm không thay thế test phần cứng và test đầu cuối.

## Trạng thái build và vận hành

Hiện chưa có lệnh build, test, flash, run hay deploy chung cho Veetee. Các lệnh bên
trong hai repo tham khảo chỉ áp dụng cho upstream. Khi source chính thức được tạo, mục
này cần được cập nhật bằng các lệnh có thể lặp lại cho mỗi phạm vi và cho test đầu cuối.
