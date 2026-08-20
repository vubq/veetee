# Moc Git source tham khao

## Muc dich

File nay pin trang thai cua hai upstream dang duoc dung de nghien cuu Veetee. Moc Git
giup AI va contributor biet tai lieu da duoc khao sat tren commit nao, so sanh voi
upstream moi va danh gia thay doi truoc khi cap nhat tai lieu hoac port code.

Hai repo tham khao la read-only trong workspace Veetee. Khong sua code/tai lieu, khong
tao artifact va khong thuc hien thao tac Git ghi trong cac repo nay.

## Moc hien tai

Moc duoc ghi nhan ngay 2026-08-20. Ca hai worktree deu sach tai thoi diem ghi nhan.

| Pham vi | Remote | Branch | Commit | Commit time | Tieu de |
| --- | --- | --- | --- | --- | --- |
| Firmware | `https://github.com/78/xiaozhi-esp32.git` | `main` | `d6f6b642977940b862f6f3026c3915df75d388b6` | `2026-08-19T14:04:52+08:00` | `feat(m5stack-stopwatch): support display brightness control (#2189)` |
| Server | `https://github.com/xinnan-tech/xiaozhi-esp32-server.git` | `main` | `e1876f1ce19cad6e7bfd7c80e41dc56b2e858dd5` | `2026-08-18T16:31:48+08:00` | `Merge pull request #3315 from xinnan-tech/fix-model-name` |

## Quy trinh doi chieu update

1. Doc moc hien tai trong file nay.
2. Dung lenh read-only de ghi nhan `HEAD`, branch, remote, status va commit time tai
   local reference.
3. Neu can biet upstream moi, truy van remote bang `git ls-remote` hoac GitHub API ma
   khong fetch/pull vao repo tham khao.
4. So sanh commit range bang API, clone tam o ngoai workspace hoac mot noi duoc phep;
   khong checkout/reset/pull repo tham khao.
5. Danh gia thay doi theo firmware, server va contract dung chung.
6. Neu chap nhan moc tham khao moi, giu lich su moc cu trong muc nhat ky ben duoi, cap
   nhat bang moc hien tai va cap nhat cac tai lieu bi anh huong.

## Lenh read-only cho phep

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git remote -v
git log -1 --format='%H%n%cI%n%s'
git diff <old-commit>..<new-commit> -- <path>
git show <commit>:<path>
git ls-remote <remote-url> refs/heads/main
```

`git diff` va `git show` chi hop le khi cac object da ton tai local. Khong fetch object
vao hai repo tham khao; neu can, dung clone tam ben ngoai workspace.

## Nhat ky moc

| Ngay ghi nhan | Firmware | Server | Ghi chu |
| --- | --- | --- | --- |
| 2026-08-20 | `d6f6b642977940b862f6f3026c3915df75d388b6` | `e1876f1ce19cad6e7bfd7c80e41dc56b2e858dd5` | Moc ban dau; hai worktree sach |
