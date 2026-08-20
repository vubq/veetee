# Tổng quan kiến trúc firmware tham khảo

## Phạm vi

Tài liệu này mô tả kiến trúc đang tồn tại trong `xiaozhi-esp32` để làm dữ liệu đầu vào
cho thiết kế Veetee. Nó không định nghĩa cấu trúc thư mục tương lai của Veetee.

## Thành phần chính

| Thành phần | Trách nhiệm quan sát được |
| --- | --- |
| `app_main()` | Khởi tạo NVS, tạo `Application`, gọi `Initialize()` và `Run()` |
| `Application` | Điều phối lifecycle, event loop, protocol, audio, UI và activation |
| `DeviceStateMachine` | Kiểm tra transition và phát callback khi state thay đổi |
| `AudioService` | Input/output audio, wake word, VAD, AEC, Opus và các queue |
| `Protocol` | Hợp đồng transport-neutral cho JSON và audio |
| `WebsocketProtocol` | JSON text frame và Opus binary frame trên một kết nối |
| `MqttProtocol` | MQTT control và UDP audio mã hóa |
| `Board` | Biến hardware/network cụ thể thành một interface chung |
| `McpServer` | Công bố và thực thi tool trên thiết bị bằng JSON-RPC 2.0 |
| `Ota`, `Assets`, `Settings` | Nâng cấp firmware, asset partition và lưu cấu hình NVS |

## Vòng đời khởi động

```text
app_main
  -> khởi tạo NVS
  -> Application::Initialize
       -> Board / Display / AudioService
       -> callback audio và state
       -> MCP tools
       -> callback network
       -> StartNetwork (bất đồng bộ)
  -> Application::Run
       -> chờ event bits
       -> xử lý callback đã Schedule
       -> activation / OTA / asset
       -> khởi tạo transport
       -> idle và các phiên hội thoại
```

`Application::Run()` là vòng lặp chính và không trả về. Callback từ network, audio hoặc
task khác không nên sửa trực tiếp state ứng dụng; upstream đưa chúng về main task qua
`Application::Schedule()` hoặc event bits.

## Ranh giới core và board

Core chỉ truy cập phần cứng qua `Board::GetInstance()` và các interface như
`AudioCodec`, `Display`, `Led`, `Camera`, `Backlight`, `NetworkInterface`. Các khả năng
camera, màn hình, pin và backlight có thể không tồn tại; getter mặc định có thể trả về
`nullptr` hoặc giá trị không hỗ trợ.

Mỗi bản build upstream chọn đúng một board factory:

```text
boards/**/config.json
  -> scripts/build.py
  -> Kconfig.projbuild
  -> CMakeLists.txt
  -> board source + config.h
  -> DECLARE_BOARD(BoardClass)
```

`DECLARE_BOARD` tạo hàm `create_board()`. Nếu nhiều implementation cùng được link vào
một binary thì hợp đồng singleton bị phá vỡ. Danh tính board cũng liên quan OTA, vì vậy
một pinout mới nên là board/variant mới thay vì sửa âm thầm board cũ.

## Mô hình đồng thời

- Main task xử lý state và lifecycle.
- Audio input, audio output và Opus codec chạy ở các task riêng.
- Network callback có thể đến từ task/driver khác.
- Queue audio đều có giới hạn để tránh tăng bộ nhớ không kiểm soát.
- Callback state được copy ra ngoài mutex trước khi invoke, tránh giữ lock trong code
  của listener.

Hệ quả thiết kế: logic trên đường audio và main loop không nên block; thao tác I/O dài,
download hoặc model processing cần được tách task và đưa kết quả về bằng event.

## Các điểm mở cần quyết định cho Veetee

- Chọn một hay nhiều transport, và hợp đồng chung giữa chúng.
- Board abstraction có cần hỗ trợ đa chip/đa màn hình ngay từ đầu hay không.
- Wake word, VAD và AEC nằm trên thiết bị hay server.
- Có cần asset partition riêng và dynamic glyph hay không.
- State machine nào là tối thiểu cho sản phẩm Veetee.
- MCP tool nào được phép cho AI, tool nào chỉ người dùng được phép gọi.

## Source đối chiếu

- `../references/xiaozhi-esp32/main/main.cc`
- `../references/xiaozhi-esp32/main/application.h`
- `../references/xiaozhi-esp32/main/application.cc`
- `../references/xiaozhi-esp32/main/boards/common/board.h`
- `../references/xiaozhi-esp32/main/CMakeLists.txt`
- `../references/xiaozhi-esp32/docs/custom-board.md`
