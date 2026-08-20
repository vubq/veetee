# Mốc Git source tham khảo

## Mục đích

File này pin trạng thái của hai upstream đang được dùng để nghiên cứu Veetee. Mốc Git
giúp AI và contributor biết tài liệu đã được khảo sát trên commit nào, so sánh với
upstream mới và đánh giá thay đổi trước khi cập nhật tài liệu hoặc port code.

Hai repo tham khảo là read-only đối với source tracked và Git history. Không sửa
code/tài liệu tracked và không thực hiện thao tác Git ghi trong các repo này. Build,
flash và runtime artifact được phép theo `server-first-development.md`, nhưng phải được
ignore và không được commit.

## Mốc hiện tại

Mốc được ghi nhận ngày 2026-08-20. Cả hai worktree đều sạch tại thời điểm ghi nhận.

| Phạm vi | Remote | Branch | Commit | Commit time | Tiêu đề |
| --- | --- | --- | --- | --- | --- |
| Firmware | `https://github.com/78/xiaozhi-esp32.git` | `main` | `d6f6b642977940b862f6f3026c3915df75d388b6` | `2026-08-19T14:04:52+08:00` | `feat(m5stack-stopwatch): support display brightness control (#2189)` |
| Server | `https://github.com/xinnan-tech/xiaozhi-esp32-server.git` | `main` | `e1876f1ce19cad6e7bfd7c80e41dc56b2e858dd5` | `2026-08-18T16:31:48+08:00` | `Merge pull request #3315 from xinnan-tech/fix-model-name` |

## Quy trình đối chiếu update

1. Đọc mốc hiện tại trong file này.
2. Dùng lệnh read-only để ghi nhận `HEAD`, branch, remote, status và commit time tại
   local reference.
3. Nếu cần biết upstream mới, truy vấn remote bằng `git ls-remote` hoặc GitHub API mà
   không fetch/pull vào repo tham khảo.
4. So sánh commit range bằng API, clone tạm ở ngoài workspace hoặc một nơi được phép;
   không checkout/reset/pull repo tham khảo.
5. Đánh giá thay đổi theo firmware, server và contract dùng chung.
6. Nếu chấp nhận mốc tham khảo mới, giữ lịch sử mốc cũ trong mục nhật ký bên dưới, cập
   nhật bảng mốc hiện tại và cập nhật các tài liệu bị ảnh hưởng.

## Lệnh read-only cho phép

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

`git diff` và `git show` chỉ hợp lệ khi các object đã tồn tại local. Không fetch object
vào hai repo tham khảo; nếu cần, dùng clone tạm bên ngoài workspace.

## Nhật ký mốc

| Ngày ghi nhận | Firmware | Server | Ghi chú |
| --- | --- | --- | --- |
| 2026-08-20 | `d6f6b642977940b862f6f3026c3915df75d388b6` | `e1876f1ce19cad6e7bfd7c80e41dc56b2e858dd5` | Mốc ban đầu; hai worktree sạch |
