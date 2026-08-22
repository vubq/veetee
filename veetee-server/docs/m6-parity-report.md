# Báo cáo thực hiện Mốc 6

## Trạng thái

Mốc 6 đã hoàn tất backend, Console responsive và migration runtime cho M6.1, M6.4–M6.8.
Tài liệu này chưa phải bàn giao Cổng 6 vì M6.2 còn thiếu nguồn consent để nối recorder
vào realtime pipeline mà không vi phạm nguyên tắc transcript opt-in.

## Quyết định đã khóa

- Provider enable/disable/default là trạng thái toàn server do admin quản lý; agent chỉ
  chọn model/voice thuộc catalog đang bật.
- Transcript là opt-in theo consent có version, retention mặc định 30 ngày và delete là
  hard delete trong transaction. Raw audio không được lưu.
- Knowledge/RAG bản đầu dùng PostgreSQL full-text search; chưa thêm `pgvector`, embedding
  model, Redis hoặc broker.
- External MCP chỉ được gọi qua HTTPS host allowlist; loopback chỉ dành cho MCP local.
  Weather adapter dùng Open-Meteo, không cần secret.
- Mọi device tool call từ Console cần confirmation một lần, TTL 60 giây và bind exact
  arguments.
- User mới dùng reset token một lần; token plaintext chỉ được hiển thị lúc tạo, database
  chỉ lưu hash và expiry.
- Hỗ trợ quota LLM token/ngày, TTS character/ngày, tool call/phút và RAG ingest byte/tháng;
  tất cả mặc định tắt cho tới khi admin cấu hình.

## Ma trận parity

| Capability | Trạng thái | Bằng chứng hiện tại |
| --- | --- | --- |
| M6.1 Provider management | Backend và Console hoàn tất | Global state/default, optimistic version, RBAC admin, runtime health không inference, model validation và audit; Console owner đọc catalog, admin mutation có role gate |
| M6.2 History và agent lifecycle | Backend persistence/API hoàn tất; realtime recorder chưa nối | Consent-gated turns, JSON export, hard delete, retention purge, template/tag, immutable snapshot và restore; device handshake/session/Console chưa có nguồn consent versioned để gọi recorder an toàn |
| M6.3 Voice | Chờ đánh giá adapter | Chưa expose selector/preview khi runtime chưa có voice catalog thật |
| M6.4 Knowledge/RAG | Backend và Console hoàn tất | Dataset/document/chunk, bounded UTF-8 upload, PostgreSQL FTS, citation/provenance, composite tenant constraint, injection delimiting, hard delete và Console upload/search/assignment |
| M6.5 Correction/context | Backend và Console hoàn tất | Versioned exact/phrase rules và preview; typed provider timeout/cache/provenance; correction và context đã nối vào realtime pipeline |
| M6.6 Tool ecosystem | Backend và Console hoàn tất | Internal server MCP catalog; external MCP HTTPS exact-host allowlist, DNS/IP pinning chống SSRF, default-deny agent permission/rate limit/timeout/audit; Open-Meteo typed adapter |
| M6.7 Device tools | Backend và Console hoàn tất | Initialize/list pagination/call correlated theo live session; owner/device/capability gate; confirmation hash một lần TTL 60s bind exact arguments; Console giữ session ID và xóa token tạm sau xác nhận/hủy/lỗi |
| M6.8 Administration | Backend và Console hoàn tất | Admin-only user/status/role và one-time reset; typed optimistic settings; bounded audit search; atomic default-off quota cho LLM/TTS/tool/RAG đã nối runtime; `403` hiện role gate không logout |
| M6.9 MQTT/UDP | Không áp dụng đã duyệt | WebSocket đáp ứng vận hành local; không mở thêm transport/security boundary |
| M6.10 Client coverage | Đã khóa | Web responsive là client quản trị; không tạo mobile app riêng |

## Bằng chứng kiểm thử tích lũy

- Backend sau M6.1–M6.2: `427/427` test pass.
- Ruff và mypy pass trên 92 source files.
- Namespace scan thường và `--all` pass.
- Hai repo `references/` giữ clean.
- M6.4 focused: bounded upload, duplicate SHA, tenant isolation, FTS citation,
  injection delimiting, cascade cleanup và migration fail-closed đều pass.
- M6.5 focused: correction semantics/version/preview, context timeout/cache tenant-version
  isolation, injection boundary và realtime pipeline regressions đều pass.
- Backend sau M6.4–M6.5: `434/434` test pass; Ruff pass; mypy pass trên 100 source
  files; namespace scan thường và `--all` pass.
- Backend sau M6.6: `442/442` test pass; Ruff pass; mypy pass trên 106 source files;
  focused SSRF/redirect/peer pinning/timeout/size/JSON-RPC/weather/permission/rate-limit/
  tenant/audit tests và namespace scan đều pass.
- M6.7 focused: parser/envelope strict, initialize-once, correlation/session isolation,
  timeout/disconnect/duplicate/stale cleanup, shared WebSocket send lock, confirmation
  expiry/replay/collision/store bound, strict argument/result JSON và PostgreSQL-backed
  Console-to-live-device list/prepare/call/audit flow đều pass. Backend sau M6.7:
  `450/450` test pass; Ruff pass; mypy pass trên 110 source files; namespace scan thường
  và `--all` pass; hai repo references clean.
- M6.8 focused: admin RBAC, one-time reset/replay/expiry, self/last-admin lockout,
  suspension revoke session, typed setting/version, bounded audit filter, quota default-off,
  UTC windows, tenant isolation, 8-way atomic concurrency, RAG pre-ingest reject và realtime
  LLM pre-provider reject đều pass. Backend sau M6.8: `459/459` test pass; Ruff pass; mypy
  pass trên 112 source files; namespace scan thường và `--all` pass.
- Console M6: type-check và production build pass; Playwright `40/40` pass trên desktop
  1440 x 900 và mobile 390 x 844. Scenario gồm điều hướng mọi view, Knowledge upload/search,
  correction preview, Device MCP list/prepare/explicit-confirm/call, exact `session_id`, token
  không render, admin `403` giữ phiên và kiểm tra horizontal page overflow/browser error.
- Runtime PostgreSQL `veetee` đã áp migration `005`–`010`; ledger có đủ `001`–`010`, bảng
  Knowledge/quota tồn tại và sáu typed system setting mặc định đã được seed.
- QA cuối checkpoint: backend `459/459` pass, Ruff pass, mypy pass trên 112 source files,
  namespace scan thường và `--all` pass; hai repo references clean.

## Điểm cần quyết định trước Cổng 6

`ConversationRecorder` và test consent-gated đã có, nhưng realtime pipeline không có nguồn
`transcript_consent`/`consent_version`. Device handshake không mang consent; Console cũng
không có workflow tạo conversation shell trước khi bật consent. Vì transcript đã khóa là
opt-in, server không được mặc định bật ghi lịch sử. Cần chốt nơi người dùng cấp consent
versioned (chính sách theo agent/device trong Console hoặc mở rộng contract session) trước
khi nối recorder vào pipeline.

## Mục không áp dụng đã duyệt

- Voice clone và voiceprint/speaker recognition.
- Address book và device calling khi firmware/UX chưa có capability được duyệt.
- Home Assistant, search, music và news khi chưa có use case/provider cụ thể.
- MQTT control và UDP audio.
- Mobile console riêng.
- Spawn/restart server từ device message.
