# Veetee Server

## Trang thai hien tai

`veetee-server` la khong gian chuan bi cho server cua Veetee. Hien tai chua co source,
service, database schema, deployment manifest hay API chinh thuc cua Veetee. Thu muc
moi chi gom tai lieu khao sat va source tham khao.

Source trong `references/xiaozhi-esp32-server` la upstream de nghien cuu, khong phai
backend dang van hanh cua Veetee va khong ap dat Python, Java, Vue hay deployment model
cho du an.

## Cau truc

```text
veetee-server/
|-- README.md       # Diem bat dau cho nguoi dung
|-- AGENTS.md       # Huong dan thao tac cho AI/contributor
|-- docs/           # Ghi chu ky thuat rut ra tu source tham khao
`-- references/     # Upstream chi dung de doc, doi chieu va thu nghiem rieng
```

Source va thu muc van hanh se chi duoc tao khi co yeu cau va quyet dinh cu the. Khong
co service nao trong `references/` duoc mac dinh coi la service cua Veetee.

## Bat dau tu dau

1. Doc [muc luc tai lieu](docs/README.md).
2. Doc [tong quan kien truc tham khao](docs/architecture.md).
3. Chon tai lieu theo cong viec:

| Cong viec | Tai lieu nen doc |
| --- | --- |
| Service boundary, deployment | [Tong quan kien truc](docs/architecture.md) |
| WebSocket, audio session, AI flow | [Realtime AI pipeline](docs/realtime-ai-pipeline.md) |
| Device protocol, HTTP, manager API | [Giao thuc va API](docs/protocols-and-apis.md) |
| Model/provider/config | [Provider va cau hinh](docs/providers-and-configuration.md) |
| Auth, van hanh, scale, test | [Bao mat va kiem thu](docs/security-operations-testing.md) |

4. Doi chieu source trong `references/xiaozhi-esp32-server` neu can xac minh.
5. Truoc khi viet code, chot ro pham vi service, du lieu, giao thuc va cach trien khai
   ma Veetee thuc su can.

## Nguyen tac thao tac

- Code moi cua Veetee phai nam ngoai `references/`.
- Chi sua `references/` khi co yeu cau ro rang ve patch/fork upstream.
- Khong mac dinh copy monorepo hay chay full stack tham khao.
- Thay doi device protocol phai duoc doi chieu dong thoi voi `../veetee-firmware`.
- API key, token, database password va service secret khong duoc ghi vao source/tai lieu.
- Endpoint, port va provider trong `docs/` la gia tri quan sat, chua phai contract Veetee.
- Moi API/service chinh thuc can co ownership, cau hinh, migration, test va cach van
  hanh duoc tai lieu hoa.

## Quy trinh cho mot cong viec moi

```text
yeu cau
  -> xac dinh domain/service bi anh huong
  -> doc docs lien quan
  -> doi chieu upstream neu can
  -> chot contract va du lieu Veetee
  -> tao source ngoai references
  -> test contract/integration/security
  -> cap nhat tai lieu va cach van hanh
```

Hien chua co lenh run/build/test cua Veetee. Docker Compose, Maven, npm, pnpm va Python
command trong upstream chi dung de nghien cuu upstream, khong phai quy trinh Veetee.

## Quan he voi firmware

Server va firmware cung so huu cac contract:

- Device identity, binding va authentication.
- WebSocket/MQTT session va reconnect.
- JSON control message va binary audio frame.
- Opus parameters, AEC timestamp va backpressure.
- MCP capability/tool authorization.
- Activation, OTA discovery, rollout va reporting.

Thay doi contract chung can co version, test vector va cap nhat tai lieu o ca hai muc.
