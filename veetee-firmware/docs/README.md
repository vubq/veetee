# Ghi chu ky thuat firmware

## Muc dich

Thu muc nay tong hop cac y tuong va giao dien quan trong tu source tham khao
`../references/xiaozhi-esp32`. Day la tai lieu nghien cuu de ho tro thiet ke firmware
Veetee, khong phai dac ta kien truc chinh thuc va khong khang dinh Veetee se ke thua
toan bo cach trien khai cua upstream.

Khi source tham khao va tai lieu nay khac nhau, source tham khao la can cu cho hanh vi
upstream. Khi Veetee co quyet dinh kien truc rieng, can ghi quyet dinh do trong tai lieu
Veetee va khong sua lai lich su khao sat de bien no thanh dac ta.

## Danh muc

| Tai lieu | Noi dung |
| --- | --- |
| [Tong quan kien truc](architecture.md) | Thanh phan, vong doi, phan tach core va phan cung |
| [Giao dien va phuong thuc](interfaces.md) | Cac class, callback va hop dong quan trong |
| [Am thanh va trang thai](audio-and-state.md) | Audio pipeline, task, queue, state machine va AEC |
| [Giao thuc thiet bi-server](device-server-protocol.md) | WebSocket, MQTT/UDP, JSON, Opus va MCP |
| [Khoi tao, OTA va cau hinh](provisioning-ota-config.md) | Network provisioning, activation, OTA, asset va NVS |

## Ban do source tham khao

| Pham vi | Vi tri upstream |
| --- | --- |
| Dieu phoi ung dung | `../references/xiaozhi-esp32/main/application.*` |
| Trang thai thiet bi | `../references/xiaozhi-esp32/main/device_state*` |
| Audio | `../references/xiaozhi-esp32/main/audio/` |
| Giao thuc mang | `../references/xiaozhi-esp32/main/protocols/` |
| Board abstraction | `../references/xiaozhi-esp32/main/boards/common/` |
| Board cu the | `../references/xiaozhi-esp32/main/boards/` |
| MCP tren thiet bi | `../references/xiaozhi-esp32/main/mcp_server.*` |
| OTA, asset, NVS | `../references/xiaozhi-esp32/main/ota.*`, `assets.*`, `settings.*` |
| Tai lieu giao thuc goc | `../references/xiaozhi-esp32/docs/` |

## Nguyen tac su dung

- Dung tai lieu nay de tim nhanh diem vao va hop dong can doi chieu.
- Kiem tra source thuc te truoc khi port mot chi tiet nhay cam nhu wire format, bo nho,
  ownership, timeout hoac transition trang thai.
- Khong sua code trong `references/` khi phat trien Veetee, tru khi co yeu cau ro rang.
- Cac gia tri 16 kHz, 24 kHz, Opus 60 ms, timeout va kich thuoc queue la gia tri quan
  sat tu upstream hien tai, chua phai chuan bat buoc cua Veetee.
