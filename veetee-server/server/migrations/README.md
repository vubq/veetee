# Veetee migrations

Migrations run directly against local PostgreSQL. They are SQL-only and must be applied
in a transaction. The first migration creates control-plane ownership, agent configuration,
device assignment, memory and audit tables. The second migration adds provider and
conversation metadata without storing provider secrets.

Apply locally:

```bash
psql -d veetee -f migrations/001_control_plane.sql
psql -d veetee -f migrations/002_runtime_control_plane.sql
psql -d veetee -f migrations/004_login_rate_limit.sql
psql -d veetee -f migrations/005_m6_foundation_providers.sql
psql -d veetee -f migrations/006_m6_agent_lifecycle_history.sql
psql -d veetee -f migrations/007_m6_knowledge_rag.sql
psql -d veetee -f migrations/008_m6_corrections_context.sql
```

Rollback only a local/test database:

```bash
psql -d veetee -f migrations/001_control_plane.down.sql
psql -d veetee -f migrations/002_runtime_control_plane.down.sql
psql -d veetee -f migrations/004_login_rate_limit.down.sql
psql -d veetee -f migrations/008_m6_corrections_context.down.sql
psql -d veetee -f migrations/007_m6_knowledge_rag.down.sql
psql -d veetee -f migrations/006_m6_agent_lifecycle_history.down.sql
psql -d veetee -f migrations/005_m6_foundation_providers.down.sql
```

The migration stores no provider secret. Provider credentials remain environment/secret
references and are never returned by the control-plane API.

Local control plane smoke test uses `VEETEE_PERSISTENCE_ENABLED=true`,
`VEETEE_DATABASE_DSN=dbname=veetee`, and bootstrap credentials supplied only through the
process environment. The default application keeps persistence disabled for existing local
device tests.

Control-plane integration tests must use a separate database named `veetee_test` (or a
DSN containing that name through `VEETEE_TEST_DATABASE_DSN`). Tests fail closed instead
of truncating the runtime `veetee` database.

## M5 activation và OTA

Áp migration sau M4 bằng database owner local:

```bash
psql -d veetee -f migrations/003_device_activation_ota.sql
```

Migration 003 preflight duplicate trước khi đổi uniqueness, thêm global uniqueness cho
`device_id`, activation TTL, bind quota/receipt TTL và artifact/release bất biến theo
tenant. Nếu dữ liệu M4 có duplicate `device_id`, migration báo rõ các ID xung đột rồi
rollback toàn bộ để người vận hành xử lý ownership thủ công; migration không tự xóa dữ
liệu. Down migration cũng từ chối khi đã có dữ liệu M5 để tránh mất dữ liệu.

## M4/M5 audit hardening: login rate limit

```bash
psql -d veetee -f migrations/004_login_rate_limit.sql
```

Migration 004 tạo `veetee_login_attempts` lưu bộ đếm đăng nhập thất bại theo SHA-256
của email đã chuẩn hóa (không bao giờ lưu email thô) trong một time window cấu hình qua
`VEETEE_LOGIN_RATE_LIMIT` và `VEETEE_LOGIN_RATE_WINDOW_SECONDS`. Bảng chỉ chứa counter
quota nên down migration drop an toàn, không cần fail closed như migration 003.

## M6.4 Knowledge/RAG

Migration 007 tạo dataset, document, chunk PostgreSQL FTS và liên kết agent-dataset.
Composite foreign key giữ `owner_user_id` nhất quán xuyên dataset, document, chunk và
agent; upload chỉ nhận UTF-8 `text/plain` hoặc `text/markdown`. Down migration từ chối
khi còn dữ liệu knowledge để tránh mất dữ liệu.

## M6.5 Correction và context provider

Migration 008 tạo correction set/rule có version và cấu hình context provider theo agent.
Rule chỉ hỗ trợ `exact` hoặc `phrase`; provider config có version để cache không dùng lại
kết quả từ cấu hình cũ. Down migration từ chối khi còn correction hoặc context config.
