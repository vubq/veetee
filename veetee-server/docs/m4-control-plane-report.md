# Báo cáo Mốc 4: Control plane và PostgreSQL

## Đã triển khai

- PostgreSQL 16 local chạy trực tiếp, không Docker.
- SQL migration có version table, idempotency và rollback cho control-plane foundation
  và runtime metadata.
- User owner/admin schema, local session bearer token, password hash bằng scrypt và
  session expiry/revocation fields.
- Tenant-scoped agent CRUD với role prompt, personality, address style, language,
  detail level, response style, model/voice, intent, tool policy và memory policy.
- Optimistic concurrency bằng `version` và `expected_version`; conflict trả HTTP 409.
- Memory list/create/forget/delete-all có owner scope và audit event.
- Device/conversation metadata read API; online device state không suy diễn từ database.
- Provider catalog read-only chỉ liệt kê adapter/model đã đăng ký; không nhận hoặc trả
  provider key.
- Console gọi API agent thật, có login form, loading/error state và lưu role prompt qua
  optimistic version. Access token chỉ giữ trong runtime memory, không lưu localStorage.

## Lệnh migration

```bash
psql -d veetee -f server/migrations/001_control_plane.sql
psql -d veetee -f server/migrations/002_runtime_control_plane.sql
```

## Kiểm thử

- Backend full pytest: 395/395 pass.
- Ruff pass.
- Mypy strict pass.
- PostgreSQL integration: migration idempotency, login, agent CRUD, tenant auth,
  optimistic conflict, memory CRUD/audit và backup/restore schema pass.
- Console `npm run type-check`: pass.
- Console `npm run build`: pass.
- Không có thay đổi trong hai repo `references`.

## Giới hạn còn lại

- Bootstrap admin hiện dùng environment local; production identity provider, refresh
  rotation, CSRF/CORS policy đầy đủ và rate limit cần harden tiếp trước production.
- Device online state cần adapter đọc `DeviceSessionRegistry` thay vì placeholder false.
- History hiện lưu metadata conversation; raw transcript/audio không được lưu mặc định.
- Console History/Device dialog vẫn cần nối các read API tương ứng ở bước hoàn thiện UI.
- Agent config đã persistence nhưng runtime conversation chưa reload immutable snapshot
  theo agent ở mỗi generation; cần hoàn thiện consistency hook trước khi coi production-ready.
