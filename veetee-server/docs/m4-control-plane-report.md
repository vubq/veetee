# Báo cáo Mốc 4: Control plane và PostgreSQL

## Đã triển khai

- PostgreSQL 16 local chạy trực tiếp, không Docker.
- SQL migration có version table, idempotency và rollback cho control-plane foundation
  và runtime metadata.
- User owner/admin schema, local session bearer token, password hash bằng scrypt,
  logout/revocation, audit login và rate limit PostgreSQL theo hash định danh đã redact.
- Tenant-scoped agent CRUD với role prompt, personality, address style, language,
  detail level, response style, model/voice, intent, tool policy và memory policy.
- Optimistic concurrency bằng `version` và `expected_version`; conflict trả HTTP 409.
- Memory list/create/forget/delete-all có owner scope và audit event.
- Device/conversation metadata read API; online device state không suy diễn từ database.
- Provider catalog read-only chỉ liệt kê adapter/model đã đăng ký; agent API từ chối
  `model_id` ngoài LLM allowlist và không nhận hoặc trả provider key.
- Console có auth screen riêng và chỉ dựng app shell sau khi xác thực. Access token chỉ
  giữ trong runtime memory, không lưu localStorage; stale 401 của phiên cũ không xóa phiên
  mới và lỗi revoke được thông báo rõ sau khi token local đã xóa.
- Agent create/rename/delete, history/device và OTA workflow gọi API thật; duplicate name
  trả 409. Control no-op được ẩn hoặc disabled với nhãn `Sắp có`.

## Lệnh migration

```bash
psql -d veetee -f server/migrations/001_control_plane.sql
psql -d veetee -f server/migrations/002_runtime_control_plane.sql
psql -d veetee -f server/migrations/004_login_rate_limit.sql
```

## Kiểm thử

- Backend full pytest: 412/412 pass.
- Ruff pass.
- Mypy strict pass trên 87 source files.
- PostgreSQL integration: migration idempotency, login, agent CRUD, tenant auth,
  optimistic/duplicate conflict, model allowlist, memory CRUD/audit, login quota/audit và
  backup/restore schema pass.
- Console `npm run type-check`: pass.
- Console `npm run build`: pass.
- Playwright: 24/24 scenario pass trên desktop 1440x900 và mobile 390x844, gồm auth-only,
  login retry/logout/401 recovery, agent CRUD, bind/unbind, OTA publish, nested dialog,
  API degradation và horizontal overflow.
- Browser smoke với FastAPI/PostgreSQL thật: login 200, agents/devices/conversations 200,
  agent create 201, delete 204 và logout 204; dữ liệu QA được cleanup và audit xác nhận.
- Lighthouse đúng URL Veetee: accessibility, best practices và SEO đều 100 trên desktop
  và mobile; browser console sạch sau reload.
- Test PostgreSQL dùng database riêng `veetee_test`; full suite xác nhận số user/agent
  trong runtime database `veetee` không đổi trước và sau test.
- Không có thay đổi trong hai repo `references`.

## Giới hạn còn lại

- Bootstrap admin hiện dùng environment local; production identity provider, refresh
  rotation và CSRF/CORS production policy vẫn cần harden trước production.
- Device online state cần adapter đọc `DeviceSessionRegistry` thay vì placeholder false.
- History hiện lưu metadata conversation; raw transcript/audio không được lưu mặc định.
- Prerequisite snapshot đã nối role/profile và LLM model vào runtime theo từng turn; lượt
  đang chạy bất biến và lượt kế tiếp nhận version mới. Voice, memory, intent và tool policy
  vẫn chưa expose vì realtime stage tương ứng chưa tiêu thụ các field này.
