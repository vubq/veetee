# Huong dan AI - Veetee Firmware

## Pham vi

File nay ap dung cho moi thao tac trong `veetee-firmware/`. Day la workspace firmware
Veetee dang o giai doan nghien cuu; chua co source chinh thuc.

## Thu tu doc bat buoc

Truoc khi thuc hien cong viec firmware:

1. Doc `README.md` de nam trang thai va ranh gioi workspace.
2. Doc `docs/README.md` va tai lieu chuyen de lien quan.
3. Chi doc cac file can thiet trong `references/xiaozhi-esp32` de xac minh chi tiet.
4. Neu cong viec anh huong wire protocol, doc them
   `../veetee-server/README.md` va `../veetee-server/docs/protocols-and-apis.md`.

## Phan loai noi dung

| Vi tri | Vai tro | Quyen thao tac mac dinh |
| --- | --- | --- |
| `README.md` | Tong quan cho nguoi dung | Cap nhat khi trang thai/quy trinh doi |
| `AGENTS.md` | Quy tac cho AI/contributor | Cap nhat khi ranh gioi thao tac doi |
| `docs/` | Ghi chu va quyet dinh ky thuat | Duoc bo sung/cap nhat |
| `references/` | Source upstream tham khao | Chi doc; cam sua va cam Git ghi |
| Source Veetee tuong lai | San pham chinh thuc | Tao ngoai `references/` theo yeu cau |

## Quy tac bat buoc

- Khong mo ta `references/xiaozhi-esp32` la cau truc hay source chinh thuc cua Veetee.
- Khong sua, format, tao build artifact, commit, checkout, pull, merge, rebase, reset
  hay push trong `references/xiaozhi-esp32`.
- Chi duoc dung Git read-only trong upstream va doi chieu commit voi
  `../docs/reference-baselines.md`.
- Duoc phep commit/push va thao tac Git cho source/tai lieu firmware Veetee nam ngoai
  `references/`, theo quy tac Git tai `../AGENTS.md`.
- Khong tu tao kien truc firmware day du chi tu source tham khao. Neu lua chon chip,
  board, framework, transport hoac AEC lam thay doi huong san pham, phai neu ro va xin
  quyet dinh.
- Moi source moi phai nam ngoai `references/` va co ownership ro rang.
- Thay doi protocol phai kiem tra ca firmware va server, bao gom backward compatibility,
  version, malformed input va timeout.
- Khong coi build thanh cong la hardware validation. Bao cao ro phan nao can test tren
  board, codec, display hoac network that.
- Khong dua secret, token, Wi-Fi credential, key OTA hoac endpoint noi bo vao source va
  tai lieu mau.

## Cach xu ly theo loai cong viec

### Nghien cuu

- Doc tai lieu Veetee truoc, source upstream sau.
- Tra ve ket luan kem file/line upstream quan trong.
- Tach ro ba nhom: hanh vi quan sat, de xuat cho Veetee, diem chua duoc quyet dinh.

### Tao kien truc/source moi

- Xac nhan target chip/board va yeu cau san pham lien quan.
- Chon thay doi nho nhat giai quyet dung yeu cau.
- Tao source, build config va test ngoai `references/`.
- Tao README gan source voi lenh build/flash/test co the lap lai.
- Cap nhat `docs/` khi xuat hien contract hoac quyet dinh lau dai.

### Port tu upstream

- Ghi ro file/module nguon va commit tham khao.
- Kiem tra license va dependency truoc khi copy.
- Chi port phan can thiet; khong mang theo board/provider/feature khong dung.
- Doi ten, abstraction va config theo Veetee khi da co quyet dinh, khong duy tri vo boc
  tuong thich neu khong co nhu cau cu the.
- Them test cho hanh vi da port va ghi sai khac voi upstream.

### Sua tai lieu

- Giu nhan `tham khao` cho thong tin rut ra tu upstream.
- Danh dau ro `quyet dinh Veetee` khi mot lua chon da duoc chot.
- Cap nhat lien ket tu `README.md` neu them tai lieu cap cao.
- Doi chieu tai lieu server neu sua giao thuc chung.

## Kiem tra truoc khi ban giao

- File moi nam dung pham vi, khong nam trong `references/` ngoai y muon.
- Worktree va history cua repo `references/xiaozhi-esp32` khong bi thay doi.
- Khong ghi thong so upstream thanh yeu cau Veetee neu chua duoc chot.
- Build/test phu hop da chay; neu chua chay, noi ro ly do.
- Thay doi audio da xem xet queue, latency, interruption, reconnect va AEC.
- Thay doi board da xem xet pin, flash, partition, optional capability va OTA identity.
- Thay doi protocol da dong bo tai lieu/test server.
- Tai lieu va lenh thao tac van con dung sau thay doi.
