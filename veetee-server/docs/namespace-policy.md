# Chính sách namespace Veetee

## Mục tiêu

Namespace public và identifier của sản phẩm phải thuộc Veetee, không để lộ tên upstream
trong endpoint, package, metadata, database object, metric, log event hoặc identifier.
Firmware tham khảo chỉ cần nhận URL Veetee qua discovery/config; không dùng URL upstream
làm compatibility path.

## Bảng namespace đề xuất

Các giá trị dưới đây là **proposed**, cần người dùng duyệt tại Cổng 0 trước khi coi là
contract bất biến.

| Boundary | Giá trị đề xuất | Quy tắc |
| --- | --- | --- |
| Application name | `veetee-server` | Tên service và process display name |
| Python package | `veetee_server` | Module root; không dùng tên upstream |
| Environment prefix | `VEETEE_` | Mọi config runtime public của server |
| Database schema | `veetee` | Không dùng schema mặc định gắn tên khác |
| REST base | `/api/v1` | Versioned control plane |
| Device WebSocket | `/api/v1/devices/ws` | Device-facing session gateway |
| OTA check | `/api/v1/devices/ota/check` | Device discovery/config response |
| OTA artifact | `/api/v1/devices/ota/artifacts/{artifact_id}` | Authenticated artifact download |
| Health | `/healthz` | Liveness; response không chứa provider secret |
| Readiness | `/readyz` | Dependency readiness, mã lỗi ổn định |
| Metrics | `/metrics` | Metric name bắt đầu bằng `veetee_` |
| Log event | `veetee.*` | Event name ổn định, structured fields |
| OpenAPI title | `Veetee Server API` | Không lấy title từ upstream |
| Correlation header | `X-Veetee-Request-Id` | Request ID do server kiểm soát |

### REST resource naming

- Dùng danh từ số nhiều cho resource: `/devices`, `/sessions`, `/agents`, `/models`.
- Dùng kebab-case cho path segment và snake_case cho JSON field, trừ field wire đã được
  firmware tham khảo yêu cầu.
- Device wire `Device-Id`, `Client-Id`, `Protocol-Version` được giữ vì là header tương
  thích; không suy ra tên đó cho database column hoặc public REST resource.
- ID public là opaque; không nhúng MAC, email, provider key hoặc tên model vào URL.
- Error envelope có `code`, `message`, `request_id`; `code` bắt đầu bằng `veetee_`.

## Identifier bị cấm

Scan sản phẩm phải reject các biến thể chữ thường/chữ hoa của tên upstream và các dạng
đường dẫn tương ứng. Tối thiểu gồm:

- `xiaozhi` và `xiaozhi-*` trong package, module, endpoint, schema, metric, event, config
  key và OpenAPI metadata.
- `/xiaozhi`, `xiaozhi/`, `xiaozhi_` và hostname chứa tên đó.
- Tên upstream trong `User-Agent`, `Server`, response metadata hoặc database object.

Ngoại lệ duy nhất là tài liệu khảo sát hoặc source trong `references/`, khi mục đích là
dẫn bằng chứng upstream. Ngoại lệ phải được giới hạn theo path và không được dùng trong
runtime source.

## Boundary với wire compatibility

Giữ field/header/message cần thiết để firmware tham khảo kết nối được không đồng nghĩa
với giữ namespace upstream. Server có thể nhận `Protocol-Version`, `Device-Id`,
`Client-Id`, `hello`, `listen`, `abort`, `mcp` và binary Opus theo ma trận tương thích;
public API, package, metadata và persistence vẫn phải theo bảng Veetee ở trên.

## Kiểm tra

Chạy từ root repository:

```bash
python3 veetee-server/tools/scan_namespace.py
python3 veetee-server/tools/scan_namespace.py --all
```

Lệnh mặc định kiểm tra các root source sản phẩm đã biết. `--all` kiểm tra mọi file có
đuôi source/config/schema ngoài `references/`, generated directory và chính detector.
Markdown được loại trừ vì tài liệu nghiên cứu cần dẫn chính xác tên/path upstream; đây là
allowlist theo loại file có chủ đích, không áp dụng cho runtime source. Script không thay
đổi file, kiểm tra cả path và nội dung, không in dòng có thể chứa secret và không truy cập
secret bên ngoài repository.
