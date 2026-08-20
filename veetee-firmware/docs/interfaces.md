# Giao dien va phuong thuc quan trong

## Application

`Application` la singleton dieu phoi cap cao.

| Phuong thuc | Vai tro |
| --- | --- |
| `GetInstance()` | Lay singleton ung dung |
| `Initialize()` | Khoi tao display, audio, callback, MCP va network |
| `Run()` | Chay main event loop, khong tra ve |
| `SetDeviceState(state)` | Yeu cau transition qua state machine |
| `Schedule(callback)` | Chuyen callback tu task khac ve main task |
| `StartListening()` / `StopListening()` | Gui event thread-safe thay vi doi state truc tiep |
| `ToggleChatState()` | Bat/tat phien hoi thoai bang event |
| `WakeWordInvoke(word)` | Khoi dau flow wake word |
| `AbortSpeaking(reason)` | Dung TTS va thong bao server |
| `SendMcpMessage(payload)` | Boc MCP payload vao transport dang dung |
| `UpgradeFirmware(url, version)` | Khoi dong nang cap theo URL |
| `ResetProtocol()` | Dong channel va giai phong protocol/OTA thread-safe |

Hop dong can giu khi tham khao: state thay doi qua `SetDeviceState`; mutation tu callback
ngoai main task dung `Schedule` hoac event bit.

## Protocol

`Protocol` tach semantic cua phien audio khoi WebSocket va MQTT/UDP.

| Phuong thuc/callback | Huong | Y nghia |
| --- | --- | --- |
| `Start()` | local | Khoi tao transport nen |
| `OpenAudioChannel()` | local | Thuong luong va mo phien audio |
| `CloseAudioChannel(send_goodbye)` | local | Dong phien, tuy chon gui goodbye |
| `IsAudioChannelOpened()` | local | Kiem tra channel con hop le va chua timeout |
| `SendAudio(packet)` | device -> server | Gui mot Opus packet va metadata |
| `SendStartListening(mode)` | device -> server | Gui `listen/start` |
| `SendStopListening()` | device -> server | Gui `listen/stop` |
| `SendWakeWordDetected(word)` | device -> server | Gui `listen/detect` |
| `SendAbortSpeaking(reason)` | device -> server | Gui `abort` |
| `SendMcpMessage(message)` | hai chieu | Gui payload JSON-RPC boc trong `type=mcp` |
| `OnIncomingAudio` | server -> device | Nhan packet audio de decode/playback |
| `OnIncomingJson` | server -> device | Nhan control/event JSON |
| `OnAudioChannelOpened/Closed` | callback | Dong bo lifecycle channel voi application |
| `OnNetworkError` | callback | Bao loi transport cho UI/state |

`AudioStreamPacket` mang `sample_rate`, `frame_duration`, `timestamp` va `payload`.
Upstream khoi tao mac dinh output server 24 kHz, frame 60 ms; gia tri co the duoc thay
doi sau hello.

## AudioService

| Nhom | Phuong thuc chinh |
| --- | --- |
| Lifecycle | `Initialize(codec)`, `Start()`, `Stop()` |
| Wake/VAD | `EnableWakeWordDetection`, `EnableVoiceProcessing`, `IsVoiceDetected` |
| AEC | `EnableDeviceAec` |
| Uplink | `PopPacketFromSendQueue`, `PopWakeWordPacket` |
| Downlink | `PushPacketToDecodeQueue`, `ResetDecoder` |
| Local playback | `PlaySound` |
| Event | `SetCallbacks(AudioServiceCallbacks&)` |

Callback quan trong gom queue uplink san sang, wake word, VAD thay doi, audio test day
va playback da drain. `on_playback_drained` duoc dung de tranh bat microphone khi audio
cu van dang phat.

## Board

| Phuong thuc | Bat buoc | Ghi chu |
| --- | --- | --- |
| `GetBoardType()` | Co | Danh tinh board |
| `GetAudioCodec()` | Co | Codec input/output |
| `GetNetwork()` | Co | Network interface |
| `StartNetwork()` | Co | Ket noi bat dong bo |
| `SetNetworkEventCallback()` | Theo implementation | Bao scanning/connecting/connected/error |
| `SetPowerSaveLevel()` | Co | Low power, balanced, performance |
| `GetDisplay/Camera/Led/Backlight()` | Tuy chon | Caller phai xu ly kha nang vang mat |
| `GetBatteryLevel()` | Tuy chon | Tra level va trang thai sac/xa |
| `GetSystemInfoJson()` | Co implementation chung | Metadata cho OTA/diagnostic |
| `GetBoardJson()` / `GetDeviceStatusJson()` | Co | Capability va runtime status |

## McpServer

MCP tren device xem firmware nhu mot tool server. Tool gom `name`, `description`,
`inputSchema` va callback. Property upstream ho tro `boolean`, `integer`, `string`,
default va min/max cho integer. Gia tri tra ve co the la boolean, integer, string, JSON
hoac image base64.

Hai muc cong bo:

- Tool thuong: AI/backend co the discover va invoke.
- User-only tool: chi hien khi client yeu cau `withUserTools=true`; phu hop reboot,
  firmware upgrade hoac hanh dong dac quyen.

## Ownership va loi

- `unique_ptr<AudioStreamPacket>` the hien ownership duoc chuyen qua queue/callback.
- `cJSON*` can tuan theo ownership cua ham tao/add/delete; khong giu con tro callback
  sau khi dispatch ket thuc neu khong clone.
- Network payload can duoc validate truoc khi doc field hoac thuc thi tool.
- NVS key va board identity nen duoc xem la persistent API.

## Source doi chieu

- `../references/xiaozhi-esp32/main/application.h`
- `../references/xiaozhi-esp32/main/protocols/protocol.h`
- `../references/xiaozhi-esp32/main/audio/audio_service.h`
- `../references/xiaozhi-esp32/main/boards/common/board.h`
- `../references/xiaozhi-esp32/main/mcp_server.h`
