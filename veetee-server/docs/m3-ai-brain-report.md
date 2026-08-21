# Báo cáo Mốc 3: Bộ não AI

Ngày hoàn tất: 2026-08-21.

## Phạm vi đã triển khai

- Prompt registry typed với version, checksum, snapshot và base prompt tiếng Việt.
- Context assembler giữ thứ tự platform policy, agent role, conversation policy,
  runtime context, memory, tool contract, history và user turn.
- Memory/tool output được đóng gói là untrusted data; dữ liệu không được nâng thành
  system instruction.
- Dialogue history tách raw transcript, normalized text và text gửi model; budget
  compaction giữ nguyên nhóm assistant tool-call cùng tool result.
- Intent routing có strategy registry cho `direct_chat`, `function_call` và
  `intent_model`; strategy chưa đăng ký bị từ chối, không fallback âm thầm và không
  dùng keyword để giả lập hiểu ngôn ngữ.
- Unified tool registry có namespace, version, JSON schema, collision detection,
  allowlist/denylist, confirmation, timeout, cancellation, output bound và audit.
- MCP JSON-RPC adapter hỗ trợ `initialize`, `tools/list` pagination, `tools/call`,
  malformed request, policy và session/generation stale protection.
- Memory model có working/episodic/profile, tenant scope, provenance, confidence,
  recency, conflict resolution, retrieve, upsert, forget và delete-all.
- Memory policy nhận sensitive-pattern và transient-detector inject được; behavior
  nghiệp vụ không bị khóa trong gateway.

## Kiểm thử và bằng chứng

- `uv run pytest -q`: 387/387 pass.
- `uv run ruff check src tests`: pass.
- `uv run mypy`: pass strict trên 75 source files.
- Prompt: checksum/version, ordering và prompt-injection regression pass.
- Dialogue: transcript separation và tool hierarchy compaction pass.
- Intent: custom classifier, protocol-only fast path và no-keyword regression pass.
- Tool: collision, policy, confirmation, timeout, truncation, cancellation và audit pass.
- MCP: initialize, pagination, call, malformed JSON, policy và stale session/generation pass.
- Memory: tenant isolation, policy gate, injectable rules, conflict, ranking và deletion pass.
- `git diff --check`: pass; không có thay đổi trong hai repo `references`.

## Ranh giới và giới hạn

- Runtime chỉ khởi tạo các registry, executor và in-memory store; không tự đăng ký
  local tool có output giả như capability production.
- Local tool deterministic chỉ được dùng trong test harness; provider thật phải được
  đăng ký qua adapter có cấu hình và policy.
- MCP adapter hiện là boundary nội bộ, chưa công bố device-facing endpoint mới.
- Memory backend hiện là in-memory abstraction; PostgreSQL, migration và RBAC thuộc
  Mốc 4.
- Semantic retrieval hiện dùng scorer typed có thể thay thế; embedding provider thật
  chưa được khóa.
- Prompt/context boundary đã sẵn sàng để nối vào pipeline; việc thay thế toàn bộ
  pipeline fake bằng agent runtime và provider tool thật cần provider/config tương ứng.

## Audit an toàn

- Không commit secret, credential, raw audio hoặc transcript nhạy cảm.
- Memory và tool result được đánh dấu untrusted để chống stored prompt injection.
- Tool physical/sensitive yêu cầu confirmation và mọi execution có audit record.
- Request MCP sai schema, sai session hoặc sai generation bị từ chối.
- Không sử dụng Docker, không sửa Git/source trong `references`.
