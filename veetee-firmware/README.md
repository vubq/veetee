# Veetee Firmware

## Trang thai hien tai

`veetee-firmware` la khong gian chuan bi cho firmware cua Veetee. Hien tai chua co
source firmware chinh thuc, board target, build system hay release artifact cua Veetee.
Thu muc moi chi gom tai lieu khao sat va source tham khao.

Khong coi code trong `references/` la code cua Veetee va khong mac dinh kien truc
Veetee se giong upstream.

## Cau truc

```text
veetee-firmware/
|-- README.md       # Diem bat dau cho nguoi dung
|-- AGENTS.md       # Huong dan thao tac cho AI/contributor
|-- docs/           # Ghi chu ky thuat rut ra tu source tham khao
`-- references/     # Upstream chi dung de doc, doi chieu va thu nghiem rieng
```

Khi source Veetee duoc tao, cau truc source, test, tool va build se duoc bo sung theo
quyet dinh ky thuat cu the. Khong tao san cac thu muc rong de ap dat kien truc som.

## Bat dau tu dau

1. Doc [muc luc tai lieu](docs/README.md).
2. Doc [tong quan kien truc tham khao](docs/architecture.md).
3. Chon tai lieu theo cong viec:

| Cong viec | Tai lieu nen doc |
| --- | --- |
| Board, lifecycle, task | [Tong quan kien truc](docs/architecture.md) |
| Class, interface, callback | [Giao dien va phuong thuc](docs/interfaces.md) |
| Microphone, speaker, VAD, AEC | [Am thanh va trang thai](docs/audio-and-state.md) |
| Ket noi voi server | [Giao thuc thiet bi-server](docs/device-server-protocol.md) |
| Provisioning, activation, OTA | [Khoi tao, OTA va cau hinh](docs/provisioning-ota-config.md) |

4. Doi chieu source trong `references/xiaozhi-esp32` neu can xac minh implementation.
5. Truoc khi viet code, xac dinh ro yeu cau Veetee, target chip/board va pham vi can
   ke thua hay viet lai.

## Nguyen tac thao tac

- Code moi cua Veetee phai nam ngoai `references/`.
- Chi sua `references/` khi co yeu cau ro rang ve viec patch/fork source tham khao.
- Khong copy nguyen module upstream neu chua danh gia license, dependency, bo nho va
  kha nang tuong thich phan cung.
- Thay doi giao thuc thiet bi-server phai duoc doi chieu dong thoi voi
  `../veetee-server`.
- Thong so trong `docs/` la gia tri quan sat tu upstream, khong phai yeu cau san pham.
- Khi co quyet dinh chinh thuc, cap nhat README/tai lieu Veetee va ghi ro phan nao la
  quyet dinh, phan nao chi la tham khao.

## Quy trinh cho mot cong viec moi

```text
yeu cau
  -> xac dinh pham vi firmware
  -> doc docs lien quan
  -> doi chieu implementation upstream neu can
  -> de xuat/chot quyet dinh Veetee
  -> tao source ngoai references
  -> build/test
  -> cap nhat tai lieu
```

Hien chua co lenh build/test cua Veetee. Lenh trong source tham khao chi co gia tri khi
chay ben trong repo upstream va khong duoc ghi nhan la quy trinh build cua Veetee.

## Quan he voi server

Firmware va server dung chung cac contract sau:

- Device identity va authentication.
- Session lifecycle va feature negotiation.
- JSON control messages va binary audio.
- Opus format, sample rate, frame duration va timestamp.
- MCP tool discovery/invocation.
- Activation, endpoint discovery va OTA metadata.

Bat ky thay doi nao o cac diem nay can cap nhat tai lieu cua ca hai muc va kiem thu hai
dau cua contract.
