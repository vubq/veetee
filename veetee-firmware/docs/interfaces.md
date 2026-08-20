# Giao diện và phương thức quan trọng

## Application

`Application` là singleton điều phối cấp cao.

| Phương thức | Vai trò |
| --- | --- |
| `GetInstance()` | Lấy singleton ứng dụng |
| `Initialize()` | Khởi tạo display, audio, callback, MCP và network |
| `Run()` | Chạy main event loop, không trả về |
| `SetDeviceState(state)` | Yêu cầu transition qua state machine |
| `Schedule(callback)` | Chuyển callback từ task khác về main task |
| `StartListening()` / `StopListening()` | Gửi event thread-safe thay vì đổi state trực tiếp |
| `ToggleChatState()` | Bật/tắt phiên hội thoại bằng event |
| `WakeWordInvoke(word)` | Khởi đầu flow wake word |
| `AbortSpeaking(reason)` | Dừng TTS và thông báo server |
| `SendMcpMessage(payload)` | Bọc MCP payload vào transport đang dùng |
| `UpgradeFirmware(url, version)` | Khởi động nâng cấp theo URL |
| `ResetProtocol()` | Đóng channel và giải phóng protocol/OTA thread-safe |

Hợp đồng cần giữ khi tham khảo: state thay đổi qua `SetDeviceState`; mutation từ callback
ngoài main task dùng `Schedule` hoặc event bit.

## Protocol

`Protocol` tách semantic của phiên audio khỏi WebSocket và MQTT/UDP.

| Phương thức/callback | Hướng | Ý nghĩa |
| --- | --- | --- |
| `Start()` | local | Khởi tạo transport nền |
| `OpenAudioChannel()` | local | Thương lượng và mở phiên audio |
| `CloseAudioChannel(send_goodbye)` | local | Đóng phiên, tùy chọn gửi goodbye |
| `IsAudioChannelOpened()` | local | Kiểm tra channel còn hợp lệ và chưa timeout |
| `SendAudio(packet)` | device -> server | Gửi một Opus packet và metadata |
| `SendStartListening(mode)` | device -> server | Gửi `listen/start` |
| `SendStopListening()` | device -> server | Gửi `listen/stop` |
| `SendWakeWordDetected(word)` | device -> server | Gửi `listen/detect` |
| `SendAbortSpeaking(reason)` | device -> server | Gửi `abort` |
| `SendMcpMessage(message)` | hai chiều | Gửi payload JSON-RPC bọc trong `type=mcp` |
| `OnIncomingAudio` | server -> device | Nhận packet audio để decode/playback |
| `OnIncomingJson` | server -> device | Nhận control/event JSON |
| `OnAudioChannelOpened/Closed` | callback | Đồng bộ lifecycle channel với application |
| `OnNetworkError` | callback | Báo lỗi transport cho UI/state |

`AudioStreamPacket` mang `sample_rate`, `frame_duration`, `timestamp` và `payload`.
Upstream khởi tạo mặc định output server 24 kHz, frame 60 ms; giá trị có thể được thay
đổi sau hello.

## AudioService

| Nhóm | Phương thức chính |
| --- | --- |
| Lifecycle | `Initialize(codec)`, `Start()`, `Stop()` |
| Wake/VAD | `EnableWakeWordDetection`, `EnableVoiceProcessing`, `IsVoiceDetected` |
| AEC | `EnableDeviceAec` |
| Uplink | `PopPacketFromSendQueue`, `PopWakeWordPacket` |
| Downlink | `PushPacketToDecodeQueue`, `ResetDecoder` |
| Local playback | `PlaySound` |
| Event | `SetCallbacks(AudioServiceCallbacks&)` |

Callback quan trọng gồm queue uplink sẵn sàng, wake word, VAD thay đổi, audio test đầy
và playback đã drain. `on_playback_drained` được dùng để tránh bật microphone khi audio
cũ vẫn đang phát.

## Board

| Phương thức | Bắt buộc | Ghi chú |
| --- | --- | --- |
| `GetBoardType()` | Có | Danh tính board |
| `GetAudioCodec()` | Có | Codec input/output |
| `GetNetwork()` | Có | Network interface |
| `StartNetwork()` | Có | Kết nối bất đồng bộ |
| `SetNetworkEventCallback()` | Theo implementation | Báo scanning/connecting/connected/error |
| `SetPowerSaveLevel()` | Có | Low power, balanced, performance |
| `GetDisplay/Camera/Led/Backlight()` | Tùy chọn | Caller phải xử lý khả năng vắng mặt |
| `GetBatteryLevel()` | Tùy chọn | Trả level và trạng thái sạc/xả |
| `GetSystemInfoJson()` | Có implementation chung | Metadata cho OTA/diagnostic |
| `GetBoardJson()` / `GetDeviceStatusJson()` | Có | Capability và runtime status |

## McpServer

MCP trên device xem firmware như một tool server. Tool gồm `name`, `description`,
`inputSchema` và callback. Property upstream hỗ trợ `boolean`, `integer`, `string`,
default và min/max cho integer. Giá trị trả về có thể là boolean, integer, string, JSON
hoặc image base64.

Hai mức công bố:

- Tool thường: AI/backend có thể discover và invoke.
- User-only tool: chỉ hiện khi client yêu cầu `withUserTools=true`; phù hợp reboot,
  firmware upgrade hoặc hành động đặc quyền.

## Ownership và lỗi

- `unique_ptr<AudioStreamPacket>` thể hiện ownership được chuyển qua queue/callback.
- `cJSON*` cần tuân theo ownership của hàm tạo/add/delete; không giữ con trỏ callback
  sau khi dispatch kết thúc nếu không clone.
- Network payload cần được validate trước khi đọc field hoặc thực thi tool.
- NVS key và board identity nên được xem là persistent API.

## Source đối chiếu

- `../references/xiaozhi-esp32/main/application.h`
- `../references/xiaozhi-esp32/main/protocols/protocol.h`
- `../references/xiaozhi-esp32/main/audio/audio_service.h`
- `../references/xiaozhi-esp32/main/boards/common/board.h`
- `../references/xiaozhi-esp32/main/mcp_server.h`
