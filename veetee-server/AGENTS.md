# Huong dan AI - Veetee Server

## Pham vi

File nay ap dung cho moi thao tac trong `veetee-server/`. Workspace dang o giai doan
nghien cuu; chua co kien truc hay source server chinh thuc cua Veetee.

## Thu tu doc bat buoc

Truoc khi thuc hien cong viec server:

1. Doc `README.md` de nam trang thai va ranh gioi workspace.
2. Doc `docs/README.md` va tai lieu chuyen de lien quan.
3. Chi doc cac file can thiet trong `references/xiaozhi-esp32-server` de xac minh.
4. Neu cong viec anh huong thiet bi, doc them
   `../veetee-firmware/README.md` va
   `../veetee-firmware/docs/device-server-protocol.md`.

## Phan loai noi dung

| Vi tri | Vai tro | Quyen thao tac mac dinh |
| --- | --- | --- |
| `README.md` | Tong quan cho nguoi dung | Cap nhat khi trang thai/quy trinh doi |
| `AGENTS.md` | Quy tac cho AI/contributor | Cap nhat khi ranh gioi thao tac doi |
| `docs/` | Ghi chu va quyet dinh ky thuat | Duoc bo sung/cap nhat |
| `references/` | Source upstream tham khao | Chi doc; cam sua va cam Git ghi |
| Source Veetee tuong lai | San pham chinh thuc | Tao ngoai `references/` theo yeu cau |

## Quy tac bat buoc

- Khong mo ta monorepo upstream la cau truc chinh thuc cua Veetee.
- Khong sua, format, cai dependency, tao build artifact, commit, checkout, pull, merge,
  rebase, reset hay push trong `references/xiaozhi-esp32-server`.
- Chi duoc dung Git read-only trong upstream va doi chieu commit voi
  `../docs/reference-baselines.md`.
- Duoc phep commit/push va thao tac Git cho source/tai lieu server Veetee nam ngoai
  `references/`, theo quy tac Git tai `../AGENTS.md`.
- Khong tu chon Python/Java/Node, database, message broker, cloud provider hoac topology
  khi lua chon do anh huong kien truc san pham; phai dua ra bang chung va xin quyet dinh.
- Source moi, migration, deployment va test cua Veetee phai nam ngoai `references/`.
- Thay doi device contract phai kiem tra ca server va firmware, co version va contract
  test.
- Moi input tu device/user/provider/tool la khong tin cay; validate schema, size, quyen,
  timeout va cancellation.
- Khong dua secret that vao source, fixture, log, Docker Compose hay tai lieu.
- Khong chay migration, deploy, push image hoac goi dich vu production neu khong co yeu
  cau va pham vi ro rang.

## Cach xu ly theo loai cong viec

### Nghien cuu

- Doc tai lieu Veetee truoc, source upstream sau.
- Tach ro hanh vi upstream, de xuat Veetee va diem chua duoc quyet dinh.
- Dan kem file/line cho protocol, API, config precedence va security-sensitive behavior.

### Tao service/source moi

- Xac dinh domain ownership va boundary toi thieu.
- Chot public/device/internal API va persistence truoc khi mo rong module.
- Tao source, config, migration, test va README van hanh ngoai `references/`.
- Dung fake provider/device trong test; khong de unit test phu thuoc API key/model that.
- Cap nhat `docs/` khi them contract, data flow hoac quyet dinh lau dai.

### Port tu upstream

- Ghi ro module va commit nguon.
- Kiem tra license, CVE/dependency va nhu cau thuc te.
- Khong port toan bo full stack de lay mot tinh nang nho.
- Tach provider-specific code khoi conversation/device contract.
- Them timeout, validation, cancellation, authorization va test neu upstream thieu.
- Ghi ro sai khac Veetee so voi upstream.

### Sua giao thuc/API

- Xac dinh consumer va producer bi anh huong.
- Version wire format; quy dinh field required/optional, enum, byte order va size limit.
- Them contract/golden vector va malformed-input test.
- Doi chieu firmware, web/mobile hoac service noi bo tuong ung.
- Cap nhat `docs/protocols-and-apis.md` va tai lieu firmware neu la contract chung.

### Sua tai lieu

- Giu nhan `tham khao` cho thong tin rut ra tu upstream.
- Danh dau `quyet dinh Veetee` khi lua chon da duoc chot.
- Cap nhat `README.md` neu them tai lieu cap cao hoac lenh thao tac chinh thuc.
- Khong tai lieu hoa credential that hay default khong an toan nhu mot cau hinh de xuat.

## Kiem tra truoc khi ban giao

- File moi nam ngoai `references/` tru khi yeu cau noi nguoc lai.
- Worktree va history cua repo `references/xiaozhi-esp32-server` khong bi thay doi.
- Khong bien cong nghe/port/endpoint upstream thanh contract Veetee ngoai y muon.
- Unit, contract va integration test phu hop da chay; neu chua, noi ro.
- Connection/session cleanup, timeout, cancellation va backpressure da duoc xem xet.
- Auth, RBAC/tenant, validation, secret va audit da duoc xem xet cho API/tool.
- Migration co rollback/compatibility va khong lam mat du lieu.
- Device protocol da dong bo voi firmware.
- README, config mau va lenh van hanh van dung sau thay doi.
