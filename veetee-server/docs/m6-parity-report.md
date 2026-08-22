# Báo cáo thực hiện Mốc 6

## Trạng thái

Mốc 6 đang được triển khai. Tài liệu này là ma trận bằng chứng tích lũy; chưa phải bàn
giao Cổng 6 cho tới khi mọi capability đã duyệt đạt acceptance hoặc được người dùng phê
duyệt rõ là không áp dụng.

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
| M6.1 Provider management | Backend hoàn tất, Console chưa nối | Global state/default, optimistic version, RBAC admin, runtime health không inference, model validation và audit |
| M6.2 History và agent lifecycle | Backend hoàn tất, Console chưa nối | Consent-gated turns, JSON export, hard delete, retention purge, template/tag, immutable snapshot và restore |
| M6.3 Voice | Chờ đánh giá adapter | Chưa expose selector/preview khi runtime chưa có voice catalog thật |
| M6.4 Knowledge/RAG | Backend hoàn tất, Console chưa nối | Dataset/document/chunk, bounded UTF-8 upload, PostgreSQL FTS, citation/provenance, composite tenant constraint, injection delimiting và hard delete |
| M6.5 Correction/context | Backend hoàn tất, Console chưa nối | Versioned exact/phrase rules và preview; typed provider timeout/cache/provenance; correction và context đã nối vào realtime pipeline |
| M6.6 Tool ecosystem | Chưa triển khai | External MCP allowlist và Open-Meteo đã được duyệt |
| M6.7 Device tools | Chưa triển khai | Mọi Console call cần confirmation một lần |
| M6.8 Administration | Nền RBAC đã có | Typed actor/status/admin dependency; user/settings/audit/quota workflow chưa triển khai |
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

## Mục không áp dụng đã duyệt

- Voice clone và voiceprint/speaker recognition.
- Address book và device calling khi firmware/UX chưa có capability được duyệt.
- Home Assistant, search, music và news khi chưa có use case/provider cụ thể.
- MQTT control và UDP audio.
- Mobile console riêng.
- Spawn/restart server từ device message.
