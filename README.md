# Veetee

## Tong quan

Veetee la du an thiet bi tro ly giong noi gom hai pham vi chinh:

| Pham vi | Trach nhiem du kien | Diem bat dau |
| --- | --- | --- |
| Firmware | Phan cung, audio, ket noi, giao dien thiet bi va OTA | [veetee-firmware](veetee-firmware/README.md) |
| Server | Phien thiet bi, xu ly AI, API, quan tri va van hanh | [veetee-server](veetee-server/README.md) |

Hien tai du an dang o giai doan nghien cuu va chuan bi. Chua co source chinh thuc,
kien truc da chot, build system, database schema, deployment hay ban phat hanh Veetee.
Hai pham vi moi gom tai lieu tong hop va source upstream de tham khao.

## Cau truc hien tai

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

| Loai noi dung | Y nghia |
| --- | --- |
| `README.md` | Diem bat dau va huong dan cho nguoi dung |
| `AGENTS.md` | Ranh gioi va quy trinh thao tac cho AI/contributor |
| `docs/` | Ghi chu ky thuat rut ra tu source tham khao |
| `references/` | Repo upstream chi dung de nghien cuu va doi chieu |

Khong coi source trong `references/` la source Veetee. Cau truc source san pham se duoc
tao sau khi co yeu cau va quyet dinh ky thuat cu the.

## Cach bat dau

1. Doc tai lieu tong quan nay.
2. Xac dinh cong viec thuoc firmware, server hay contract dung chung.
3. Doc README va AGENTS cua pham vi tuong ung.
4. Doc tai lieu chuyen de trong `docs/`.
5. Chi doi chieu `references/` khi can xac minh implementation upstream.
6. Tao source Veetee ngoai `references/`, kem test va tai lieu phu hop.

## Dieu huong theo cong viec

| Cong viec | Tai lieu bat dau |
| --- | --- |
| Board, audio, state, provisioning | [Firmware](veetee-firmware/README.md) |
| WebSocket, AI pipeline, provider, API | [Server](veetee-server/README.md) |
| Giao thuc thiet bi-server | [Firmware protocol](veetee-firmware/docs/device-server-protocol.md) va [Server API](veetee-server/docs/protocols-and-apis.md) |
| Activation va OTA | [Firmware OTA](veetee-firmware/docs/provisioning-ota-config.md) va [Server security/operations](veetee-server/docs/security-operations-testing.md) |
| Bao mat dau cuoi | [Server security](veetee-server/docs/security-operations-testing.md) cung cac tai lieu protocol/OTA firmware |

## Contract dung chung

Firmware va server cung so huu cac contract sau:

- Danh tinh thiet bi, binding, authentication va credential rotation.
- Feature negotiation, session lifecycle, heartbeat, timeout va reconnect.
- JSON control message va binary audio frame.
- Opus format, sample rate, frame duration, timestamp va backpressure.
- Wake word, listening mode, interruption va AEC.
- MCP capability discovery, tool invocation va authorization.
- Provisioning, activation, endpoint discovery, OTA va rollout reporting.

Thay doi mot contract dung chung phai:

1. Xac dinh producer va consumer bi anh huong.
2. Co version va quy tac tuong thich ro rang.
3. Cap nhat tai lieu o ca firmware va server.
4. Co contract test hoac golden test vector cho hai dau.
5. Kiem tra malformed input, timeout, reconnect va security boundary.

## Nguyen tac du an

- Lua chon upstream chi la du lieu tham khao, khong phai quyet dinh Veetee.
- AI duoc phep commit, push va thuc hien cac thao tac Git can thiet cho phan Veetee nam
  ngoai `references/`, voi dieu kien kiem tra dung thay doi, branch va remote.
- Cam sua code/tai lieu va cam thao tac Git ghi trong hai repo `references/`.
- Moc commit cua hai upstream duoc luu tai
  [reference baselines](docs/reference-baselines.md) de doi chieu update sau nay.
- Khong tao kien truc rong hoac boilerplate lon truoc khi co nhu cau cu the.
- Source moi can co ownership, cach build/run/test va tai lieu ngan gon.
- Khong dua secret that vao source, log, fixture, tai lieu hay cau hinh mau.
- Moi quyet dinh chinh thuc can duoc phan biet voi ghi chu khao sat upstream.
- Build phan mem khong thay the test phan cung va test dau cuoi.

## Trang thai build va van hanh

Hien chua co lenh build, test, flash, run hay deploy chung cho Veetee. Cac lenh ben
trong hai repo tham khao chi ap dung cho upstream. Khi source chinh thuc duoc tao, muc
nay can duoc cap nhat bang cac lenh co the lap lai cho moi pham vi va cho test dau cuoi.
