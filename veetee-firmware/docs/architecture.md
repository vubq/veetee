# Tong quan kien truc firmware tham khao

## Pham vi

Tai lieu nay mo ta kien truc dang ton tai trong `xiaozhi-esp32` de lam du lieu dau vao
cho thiet ke Veetee. No khong dinh nghia cau truc thu muc tuong lai cua Veetee.

## Thanh phan chinh

| Thanh phan | Trach nhiem quan sat duoc |
| --- | --- |
| `app_main()` | Khoi tao NVS, tao `Application`, goi `Initialize()` va `Run()` |
| `Application` | Dieu phoi lifecycle, event loop, protocol, audio, UI va activation |
| `DeviceStateMachine` | Kiem tra transition va phat callback khi state thay doi |
| `AudioService` | Input/output audio, wake word, VAD, AEC, Opus va cac queue |
| `Protocol` | Hop dong transport-neutral cho JSON va audio |
| `WebsocketProtocol` | JSON text frame va Opus binary frame tren mot ket noi |
| `MqttProtocol` | MQTT control va UDP audio ma hoa |
| `Board` | Bien hardware/network cu the thanh mot interface chung |
| `McpServer` | Cong bo va thuc thi tool tren thiet bi bang JSON-RPC 2.0 |
| `Ota`, `Assets`, `Settings` | Nang cap firmware, asset partition va luu cau hinh NVS |

## Vong doi khoi dong

```text
app_main
  -> khoi tao NVS
  -> Application::Initialize
       -> Board / Display / AudioService
       -> callback audio va state
       -> MCP tools
       -> callback network
       -> StartNetwork (bat dong bo)
  -> Application::Run
       -> cho event bits
       -> xu ly callback da Schedule
       -> activation / OTA / asset
       -> khoi tao transport
       -> idle va cac phien hoi thoai
```

`Application::Run()` la vong lap chinh va khong tra ve. Callback tu network, audio hoac
task khac khong nen sua truc tiep state ung dung; upstream dua chung ve main task qua
`Application::Schedule()` hoac event bits.

## Ranh gioi core va board

Core chi truy cap phan cung qua `Board::GetInstance()` va cac interface nhu
`AudioCodec`, `Display`, `Led`, `Camera`, `Backlight`, `NetworkInterface`. Cac kha nang
camera, man hinh, pin va backlight co the khong ton tai; getter mac dinh co the tra ve
`nullptr` hoac gia tri khong ho tro.

Moi ban build upstream chon dung mot board factory:

```text
boards/**/config.json
  -> scripts/build.py
  -> Kconfig.projbuild
  -> CMakeLists.txt
  -> board source + config.h
  -> DECLARE_BOARD(BoardClass)
```

`DECLARE_BOARD` tao ham `create_board()`. Neu nhieu implementation cung duoc link vao
mot binary thi hop dong singleton bi pha vo. Danh tinh board cung lien quan OTA, vi vay
mot pinout moi nen la board/variant moi thay vi sua am tham board cu.

## Mo hinh dong thoi

- Main task xu ly state va lifecycle.
- Audio input, audio output va Opus codec chay o cac task rieng.
- Network callback co the den tu task/driver khac.
- Queue audio deu co gioi han de tranh tang bo nho khong kiem soat.
- Callback state duoc copy ra ngoai mutex truoc khi invoke, tranh giu lock trong code
  cua listener.

He qua thiet ke: logic tren duong audio va main loop khong nen block; thao tac I/O dai,
download hoac model processing can duoc tach task va dua ket qua ve bang event.

## Cac diem mo can quyet dinh cho Veetee

- Chon mot hay nhieu transport, va hop dong chung giua chung.
- Board abstraction co can ho tro da chip/da man hinh ngay tu dau hay khong.
- Wake word, VAD va AEC nam tren thiet bi hay server.
- Co can asset partition rieng va dynamic glyph hay khong.
- State machine nao la toi thieu cho san pham Veetee.
- MCP tool nao duoc phep cho AI, tool nao chi nguoi dung duoc phep goi.

## Source doi chieu

- `../references/xiaozhi-esp32/main/main.cc`
- `../references/xiaozhi-esp32/main/application.h`
- `../references/xiaozhi-esp32/main/application.cc`
- `../references/xiaozhi-esp32/main/boards/common/board.h`
- `../references/xiaozhi-esp32/main/CMakeLists.txt`
- `../references/xiaozhi-esp32/docs/custom-board.md`
