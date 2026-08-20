# Hướng dẫn AI - Dự án Veetee

## Phạm vi

File này áp dụng cho toàn bộ workspace `veetee/`. Các file `AGENTS.md` trong
`veetee-firmware/` và `veetee-server/` bổ sung quy tắc chuyên biệt cho từng phạm vi.
Khi có nhiều file hướng dẫn, phải tuân theo cả quy tắc cấp gốc và quy tắc gần file đang
thao tác nhất.

Dự án hiện đang ở giai đoạn nghiên cứu. Chưa có source hay kiến trúc chính thức của
Veetee; `references/` chỉ chứa upstream để tham khảo.

## Thứ tự đọc bắt buộc

Trước khi thực hiện công việc:

1. Đọc `README.md` tại gốc.
2. Phân loại công việc: firmware, server hay contract dùng chung.
3. Đọc `README.md` và `AGENTS.md` trong phạm vi liên quan.
4. Đọc tài liệu chuyên đề cần thiết trong `docs/`.
5. Chỉ sau đó mới đọc file cụ thể trong `references/` nếu cần đối chiếu.

Trong giai đoạn phát triển server-first hiện tại, mọi công việc server hoặc test tích hợp
thiết bị phải đọc thêm `docs/server-first-development.md`.

Nếu công việc ảnh hưởng cả hai phạm vi, phải đọc cả:

- `veetee-firmware/AGENTS.md`
- `veetee-server/AGENTS.md`
- `veetee-firmware/docs/device-server-protocol.md`
- `veetee-server/docs/protocols-and-apis.md`

## Bản đồ ownership

| Vị trí | Ownership | Thao tác mặc định |
| --- | --- | --- |
| `README.md`, `AGENTS.md` tại gốc | Tổng quan toàn dự án | Cập nhật khi trạng thái/quy trình chung đổi |
| `veetee-firmware/` | Firmware và tài liệu thiết bị | Theo AGENTS của firmware |
| `veetee-server/` | Server và tài liệu backend | Theo AGENTS của server |
| `*/docs/` | Khảo sát/quyết định kỹ thuật | Được cập nhật trong đúng phạm vi |
| `*/references/` | Upstream ngoài dự án | Cấm sửa source và Git ghi; được build/run làm test harness theo quy trình server-first |

## Quyền Git

- AI được phép sử dụng Git cho code và tài liệu Veetee nằm ngoài `references/`, bao gồm
  khởi tạo repo, tạo/chuyển branch, stage, commit, fetch, pull, merge, rebase và push khi
  cần để hoàn thành công việc.
- Trước khi commit, phải kiểm tra status/diff và chỉ đưa thay đổi đúng phạm vi vào commit.
- Trước khi push, phải xác minh branch, remote và các commit sẽ được đẩy lên.
- Không force-push, xóa branch remote, sửa lịch sử đã chia sẻ hoặc đẩy secret nếu không
  có yêu cầu rõ ràng.
- Quyền Git trên không áp dụng cho hai repo trong `references/`. Tại đó chỉ được chạy
  lệnh Git read-only để xem status, log, diff, branch, remote và commit.
- Mốc upstream được lưu tại `docs/reference-baselines.md`. Mỗi lần đối chiếu/cập nhật
  upstream phải giữ lại mốc cũ, ghi mốc mới và tóm tắt sai khác; không pull, checkout,
  reset, merge, rebase, commit hay push trực tiếp trong repo tham khảo.

## Quy tắc bắt buộc

- Tài liệu Veetee và giao tiếp với người dùng phải dùng tiếng Việt có dấu, trừ tên riêng,
  identifier, lệnh, log và thuật ngữ kỹ thuật cần giữ nguyên để bảo đảm chính xác.
- Không gọi repo upstream là source, kiến trúc, API hay quy trình chính thức của Veetee.
- Không sửa code/tài liệu tracked, format source hoặc thực hiện thao tác Git ghi trong
  hai repo `references/`. Được build, flash và chạy upstream để phục vụ test tích hợp;
  artifact/runtime state phải được ignore và không được commit.
- Source Veetee mới phải nằm ngoài `references/`.
- Không tự chốt lựa chọn ảnh hưởng lớn như board/chip, framework, ngôn ngữ, database,
  broker, cloud provider, deployment topology hoặc backward compatibility khi chưa có
  yêu cầu/bằng chứng đầy đủ.
- Trong giai đoạn phát triển hiện tại, không dùng Docker hoặc Docker Compose. Backend,
  frontend, cơ sở dữ liệu và dịch vụ phụ trợ phải chạy trực tiếp trên máy local này;
  không tự thêm container manifest hoặc quy trình vận hành bằng container.
- Không tạo bộ khung lớn chỉ để dự phòng. Ưu tiên thay đổi nhỏ nhất đáp ứng công việc.
- Bảo vệ thay đổi của người dùng và agent khác; không revert file không thuộc phạm vi.
- Không chạy thao tác external có ảnh hưởng lớn như deploy, migration production, push
  image, OTA fleet hay gọi API tốn phí nếu chưa được ủy quyền.
- Không đưa secret, credential, token, private key hoặc dữ liệu nhạy cảm vào output.

## Phân loại công việc

### Chỉ firmware

Làm việc trong `veetee-firmware/`, tuân theo `veetee-firmware/AGENTS.md`. Nếu thay đổi
wire format, identity, activation, OTA metadata hoặc MCP thì chuyển thành công việc
contract dùng chung.

### Chỉ server

Làm việc trong `veetee-server/`, tuân theo `veetee-server/AGENTS.md`. Nếu thay đổi
device-facing protocol, audio parameters, session lifecycle hoặc device command thì
chuyển thành công việc contract dùng chung.

Giai đoạn hiện tại ưu tiên phát triển server. Firmware upstream được dùng như client
tham chiếu cho tới khi Veetee có firmware riêng.

### Contract dùng chung

Phải xem cả hai đầu, không chỉ sửa một phía. Tài liệu tối thiểu cần cập nhật:

- `veetee-firmware/docs/device-server-protocol.md`
- `veetee-server/docs/protocols-and-apis.md`

Nếu liên quan activation/OTA/security, cập nhật thêm tài liệu tương ứng. Thêm test vector
chung khi source Veetee đã tồn tại.

### Nghiên cứu upstream

- Nêu rõ upstream/commit/file được khảo sát.
- Đối chiếu với mốc trong `docs/reference-baselines.md`.
- Phân biệt `hành vi quan sát`, `đề xuất Veetee` và `chưa quyết định`.
- Không biến chi tiết implementation upstream thành yêu cầu sản phẩm.
- Không sửa upstream chỉ để minh họa kết quả nghiên cứu.

### Tạo source mới

- Xác định phạm vi và ownership trước.
- Tạo cấu trúc tối thiểu cho yêu cầu hiện tại.
- Kèm README hoặc hướng dẫn build/run/test gần source.
- Thêm test theo rủi ro: unit, contract, integration, hardware hoặc end-to-end.
- Cập nhật tài liệu khi tạo contract hoặc quyết định lâu dài.

## Quy trình thực hiện

```text
đọc yêu cầu
  -> phân loại ownership
  -> đọc hướng dẫn và docs liên quan
  -> đối chiếu upstream nếu cần
  -> xác định điểm chưa được quyết định
  -> triển khai ngoài references
  -> test theo rủi ro
  -> cập nhật tài liệu
  -> báo cáo kết quả và giới hạn xác minh
```

Chỉ hỏi người dùng khi một lựa chọn còn thiếu có thể làm thay đổi đáng kể kết quả. Với
chi tiết nhỏ, có thể đưa ra giả định an toàn, ghi rõ và tiếp tục.

## Kiểm tra trước khi bàn giao

- Đã đọc đúng AGENTS theo phạm vi.
- File mới không nằm trong `references/` ngoài ý muốn.
- Không có thay đổi worktree, commit hay Git history trong hai repo tham khảo.
- Không gán nhãn chi tiết upstream thành quyết định Veetee.
- Contract dùng chung đã được xem xét ở cả firmware và server.
- Test/build phù hợp đã chạy; phần chưa xác minh được nêu rõ.
- Hardware-dependent behavior không được kết luận chỉ từ build.
- Security, secret, malformed input, timeout và cleanup đã được xem xét theo rủi ro.
- README, AGENTS và docs vẫn phản ánh đúng trạng thái sau thay đổi.
