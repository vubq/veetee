# Khoi tao mang, activation, OTA va cau hinh

## Chuoi activation tham khao

```text
network connected
  -> background ActivationTask
  -> kiem tra asset version
  -> Ota::CheckVersion
  -> xu ly server time / activation challenge / activation code
  -> Ota::Activate neu can
  -> chon MQTT config hoac WebSocket config
  -> kiem tra firmware moi
  -> idle
```

`Ota` khong chi download firmware; response cua version endpoint con co the phan phoi
transport config, server time, serial number va activation data. Day la coupling quan
trong can tach ro trong thiet ke Veetee: provisioning/config discovery va firmware OTA
co the can lifecycle, quyen va tan suat khac nhau.

## API `Ota` quan trong

| Phuong thuc | Vai tro |
| --- | --- |
| `CheckVersion()` | Goi endpoint, parse version/config/activation |
| `Activate()` | Hoan tat challenge/activation |
| `HasNewVersion()` | Co firmware moi hay khong |
| `HasMqttConfig()` / `HasWebsocketConfig()` | Transport config da nhan |
| `HasActivationCode/Challenge()` | Trang thai bind/activate |
| `StartUpgrade(callback)` | Download va flash version da discover |
| `Upgrade(url, callback)` | Nang cap truc tiep tu URL |
| `MarkCurrentVersionValid()` | Xac nhan image boot thanh cong |
| `GetCheckVersionUrl()` | Tao URL version check |

Callback upgrade nhan phan tram progress va toc do byte/giay. Image moi can duoc xac
thuc truoc khi danh dau hop le; production nen co secure boot, signed image, anti-
rollback va rollback khi boot health check that bai.

## Network provisioning

Board phat event chung cho scanning, connecting, connected, disconnected va che do
cau hinh Wi-Fi. Cellular implementation co them no-SIM, registration denied, init
failure va timeout. Core khong nen biet driver cu the.

Source tham khao co BluFi va cac board/network helper khac. Veetee can chot:

- Kenh provisioning: BLE, SoftAP, USB, app companion hay pre-provisioned.
- Cach bao ve credential khi pairing.
- Factory reset xoa gi va co lam doi `Client-Id` hay khong.
- Retry/backoff va UI khi mat mang.

## Settings va NVS

`Settings` boc NVS theo namespace/key va luu chuoi, integer, boolean. Key da phat hanh
la persistent API; doi ten/key/type can migration. Khong nen luu access token hoac Wi-Fi
credential dang plaintext neu hardware co the dung flash encryption/NVS encryption.

Cac nhom data nen duoc phan loai rieng:

| Nhom | Vi du | Policy de xuat |
| --- | --- | --- |
| Identity | device ID, client ID, serial | On dinh, co quy tac reset ro |
| Secret | Wi-Fi, token, MQTT password | Encrypt, khong log |
| Runtime config | endpoint, volume, locale | Versioned schema, validate |
| OTA state | active/pending version, rollback | Atomic va chiu mat dien |
| Cache | asset metadata, temporary state | Co the tai tao/xoa |

## Asset partition

Upstream co partition rieng cho model/font/image/audio asset va kiem tra version khi
khoi dong. Asset co the duoc download doc lap firmware. Can kiem tra kich thuoc partition,
hash, atomic switch va kha nang rollback; khong ghi de asset dang su dung.

## Checklist production

- HTTPS/TLS va pin/trust policy cho endpoint provisioning, activation va OTA.
- Chu ky firmware/asset va kiem tra hash truoc khi switch partition.
- Power-loss test o moi giai doan erase/write/activate.
- Rate limit activation va khong hien secret trong log/man hinh.
- Version schema cho response config va kha nang bo qua field moi.
- Recovery path khi endpoint tra config sai hoac ca hai transport deu that bai.
- Telemetry toi thieu cho ly do rollback, download error va boot loop.

## Source doi chieu

- `../references/xiaozhi-esp32/main/ota.h`
- `../references/xiaozhi-esp32/main/ota.cc`
- `../references/xiaozhi-esp32/main/settings.*`
- `../references/xiaozhi-esp32/main/assets.*`
- `../references/xiaozhi-esp32/main/application.cc`
- `../references/xiaozhi-esp32/docs/blufi.md`
