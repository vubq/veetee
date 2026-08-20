# Huong dan AI - Du an Veetee

## Pham vi

File nay ap dung cho toan bo workspace `veetee/`. Cac file `AGENTS.md` trong
`veetee-firmware/` va `veetee-server/` bo sung quy tac chuyen biet cho tung pham vi.
Khi co nhieu file huong dan, phai tuan theo ca quy tac cap goc va quy tac gan file dang
thao tac nhat.

Du an hien dang o giai doan nghien cuu. Chua co source hay kien truc chinh thuc cua
Veetee; `references/` chi chua upstream de tham khao.

## Thu tu doc bat buoc

Truoc khi thuc hien cong viec:

1. Doc `README.md` tai goc.
2. Phan loai cong viec: firmware, server hay contract dung chung.
3. Doc `README.md` va `AGENTS.md` trong pham vi lien quan.
4. Doc tai lieu chuyen de can thiet trong `docs/`.
5. Chi sau do moi doc file cu the trong `references/` neu can doi chieu.

Neu cong viec anh huong ca hai pham vi, phai doc ca:

- `veetee-firmware/AGENTS.md`
- `veetee-server/AGENTS.md`
- `veetee-firmware/docs/device-server-protocol.md`
- `veetee-server/docs/protocols-and-apis.md`

## Ban do ownership

| Vi tri | Ownership | Thao tac mac dinh |
| --- | --- | --- |
| `README.md`, `AGENTS.md` tai goc | Tong quan toan du an | Cap nhat khi trang thai/quy trinh chung doi |
| `veetee-firmware/` | Firmware va tai lieu thiet bi | Theo AGENTS cua firmware |
| `veetee-server/` | Server va tai lieu backend | Theo AGENTS cua server |
| `*/docs/` | Khao sat/quyet dinh ky thuat | Duoc cap nhat trong dung pham vi |
| `*/references/` | Upstream ngoai du an | Chi doc; cam sua va cam thao tac Git ghi |

## Quyen Git

- AI duoc phep su dung Git cho code va tai lieu Veetee nam ngoai `references/`, bao gom
  khoi tao repo, tao/chuyen branch, stage, commit, fetch, pull, merge, rebase va push khi
  can de hoan thanh cong viec.
- Truoc khi commit, phai kiem tra status/diff va chi dua thay doi dung pham vi vao commit.
- Truoc khi push, phai xac minh branch, remote va cac commit se duoc day len.
- Khong force-push, xoa branch remote, sua lich su da chia se hoac day secret neu khong
  co yeu cau ro rang.
- Quyen Git tren khong ap dung cho hai repo trong `references/`. Tai do chi duoc chay
  lenh Git read-only de xem status, log, diff, branch, remote va commit.
- Moc upstream duoc luu tai `docs/reference-baselines.md`. Moi lan doi chieu/cap nhat
  upstream phai giu lai moc cu, ghi moc moi va tom tat sai khac; khong pull, checkout,
  reset, merge, rebase, commit hay push truc tiep trong repo tham khao.

## Quy tac bat buoc

- Khong goi repo upstream la source, kien truc, API hay quy trinh chinh thuc cua Veetee.
- Khong sua code/tai lieu, format, cai dependency, tao artifact hoac thuc hien thao tac
  Git ghi trong hai repo `references/`.
- Source Veetee moi phai nam ngoai `references/`.
- Khong tu chot lua chon anh huong lon nhu board/chip, framework, ngon ngu, database,
  broker, cloud provider, deployment topology hoac backward compatibility khi chua co
  yeu cau/bang chung day du.
- Khong tao bo khung lon chi de du phong. Uu tien thay doi nho nhat dap ung cong viec.
- Bao ve thay doi cua nguoi dung va agent khac; khong revert file khong thuoc pham vi.
- Khong chay thao tac external co anh huong lon nhu deploy, migration production, push
  image, OTA fleet hay goi API ton phi neu chua duoc uy quyen.
- Khong dua secret, credential, token, private key hoac du lieu nhay cam vao output.

## Phan loai cong viec

### Chi firmware

Lam viec trong `veetee-firmware/`, tuan theo `veetee-firmware/AGENTS.md`. Neu thay doi
wire format, identity, activation, OTA metadata hoac MCP thi chuyen thanh cong viec
contract dung chung.

### Chi server

Lam viec trong `veetee-server/`, tuan theo `veetee-server/AGENTS.md`. Neu thay doi
device-facing protocol, audio parameters, session lifecycle hoac device command thi
chuyen thanh cong viec contract dung chung.

### Contract dung chung

Phai xem ca hai dau, khong chi sua mot phia. Tai lieu toi thieu can cap nhat:

- `veetee-firmware/docs/device-server-protocol.md`
- `veetee-server/docs/protocols-and-apis.md`

Neu lien quan activation/OTA/security, cap nhat them tai lieu tuong ung. Them test vector
chung khi source Veetee da ton tai.

### Nghien cuu upstream

- Neu ro upstream/commit/file duoc khao sat.
- Doi chieu voi moc trong `docs/reference-baselines.md`.
- Phan biet `hanh vi quan sat`, `de xuat Veetee` va `chua quyet dinh`.
- Khong bien chi tiet implementation upstream thanh yeu cau san pham.
- Khong sua upstream chi de minh hoa ket qua nghien cuu.

### Tao source moi

- Xac dinh pham vi va ownership truoc.
- Tao cau truc toi thieu cho yeu cau hien tai.
- Kem README hoac huong dan build/run/test gan source.
- Them test theo rui ro: unit, contract, integration, hardware hoac end-to-end.
- Cap nhat tai lieu khi tao contract hoac quyet dinh lau dai.

## Quy trinh thuc hien

```text
doc yeu cau
  -> phan loai ownership
  -> doc huong dan va docs lien quan
  -> doi chieu upstream neu can
  -> xac dinh diem chua duoc quyet dinh
  -> trien khai ngoai references
  -> test theo rui ro
  -> cap nhat tai lieu
  -> bao cao ket qua va gioi han xac minh
```

Chi hoi nguoi dung khi mot lua chon con thieu co the lam thay doi dang ke ket qua. Voi
chi tiet nho, co the dua ra gia dinh an toan, ghi ro va tiep tuc.

## Kiem tra truoc khi ban giao

- Da doc dung AGENTS theo pham vi.
- File moi khong nam trong `references/` ngoai y muon.
- Khong co thay doi worktree, commit hay Git history trong hai repo tham khao.
- Khong gan nhan chi tiet upstream thanh quyet dinh Veetee.
- Contract dung chung da duoc xem xet o ca firmware va server.
- Test/build phu hop da chay; phan chua xac minh duoc neu ro.
- Hardware-dependent behavior khong duoc ket luan chi tu build.
- Security, secret, malformed input, timeout va cleanup da duoc xem xet theo rui ro.
- README, AGENTS va docs van phan anh dung trang thai sau thay doi.
