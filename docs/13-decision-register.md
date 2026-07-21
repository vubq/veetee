# Veetee decision register

Tài liệu này là nơi phân biệt quyết định đã chốt, mặc định đề xuất và câu hỏi cần xác nhận trước khi giao AI triển khai. Nếu một tài liệu khác mâu thuẫn với file này, phải sửa contract/fixture hoặc tạo ADR migration; không tự chọn theo file đọc sau cùng.

## 1. Đã chốt cho blueprint

| Chủ đề | Quyết định |
|---|---|
| Repository | Hai source: `veetee-firmware` và `veetee-server`; reference Xiaozhi chỉ đọc. |
| License | Code mới của Veetee dùng MIT; code/notice bên thứ ba được giữ theo license nguồn tương ứng. |
| Firmware | ESP32-S3 N16R8, ST7789, INMP441, MAX98357A; pin map chỉ là provisional tới khi đo board thật. |
| Manager Web visual | Giữ nguyên visual/interaction của `veetee-server/prototypes/manager-web/index.html`; khi chuyển Vue chỉ thay data layer và bổ sung màn hình vận hành đã ghi trong spec. |
| Conversation | `mode=auto` là mặc định: wake/button mở assistant gate, VAD tự finalize, AI tự trả lời; không cần bấm nút lần hai. |
| Wake | Button và activation wake word mở cùng một flow; activation/interrupt detector profile tách lifecycle. |
| Wake runtime V1 | ESP-SR model pack (`srmodels.bin`), detector id nằm trong profile; runtime/operator mới chỉ qua firmware OTA. |
| Interrupt | Button interrupt là hard guarantee. Voice interrupt khi loa đang phát là best-effort cho tới khi AEC/far-end reference benchmark pass. |
| Conversation runtime | `TurnArbiter` + cancellation token + deadline + generation check; stale result không được phát TTS hay kích hoạt tool tiếp theo. |
| Admission | Audio đi qua quality/admission gate trước planner/LLM/MCP; không hard-code rule theo quạt/TV/nguồn âm thanh. |
| Timeout | Inactivity timer, provider deadlines và absolute session ceiling là các timer độc lập. |
| Transport | Native V1 dùng WebSocket; MQTT+UDP chỉ bật bằng transport policy explicit hoặc compatibility profile. |
| Device contract | Device-facing JSON/bootstrap dùng `snake_case`; canonical route nằm dưới `/veetee/...`, alias Xiaozhi ở gateway. |
| MCP | JSON-RPC 2.0, pagination, schema/range validation, tool safety class và cancellation scope theo turn. |
| Artifact | Immutable desired/reported state; firmware tự pull, verify, stage inactive slot, apply ở boundary và rollback khi health fail. |
| Dynamic code | Resource bundle chỉ chứa data/model/assets; không chứa native executable/operator tùy ý. |
| Pairing | Code 6 số là one-time handle, có TTL/attempt limit/CSPRNG/atomic consume; challenge là random nonce, không dùng MAC. |
| Domain | Không yêu cầu mua domain; dev chạy bằng LAN IP, release có thể dùng local CA/SPKI pinning hoặc tunnel/domain sau. |
| Deployment V1 | Single-node: voice-server, manager-api/web, PostgreSQL/Redis/MinIO, 9Router, Silero/ASR/TTS workers chạy cùng một máy. Provider/model traffic dùng loopback; chỉ Voice WS, Manager và Device Edge mở ra LAN. |
| Privacy | Raw audio không lưu mặc định; transcript/voiceprint có retention/consent riêng. |
| Speech AI placement | ASR, Silero VAD và VieNeu-TTS chạy local trên voice-server; ESP32 chỉ capture/playback/Opus/wake/interrupt. |
| ASR baseline | Sherpa-ONNX Zipformer Vietnamese 30M INT8 là primary; ChunkFormer-CTC-Large-Vie chỉ re-decode khi confidence/ổn định thấp hoặc policy yêu cầu chất lượng cao. Không chạy cả hai trên mọi utterance. |
| TTS baseline | VieNeu-TTS v3 Turbo là primary `vi-VN`; phải probe streaming/batch và benchmark trước khi freeze capability. Cloud TTS không tự bật trong privacy profile local-only. |
| VAD baseline | Silero VAD (`silero-local`) chạy server để speech/endpoint; không coi VAD là noise classifier hay semantic admission. |
| LLM baseline | 9Router `v0.5.40` local, endpoint `/v1`, model smoke-test `cx/gpt-5.4-mini`; dùng như development/LAN adapter. Production vẫn phải giữ adapter thay thế (official API/self-hosted). ChatGPT Plus/Codex OAuth không được coi là OpenAI Platform API key. |

## 2. Mặc định đề xuất để bắt đầu code

| Chủ đề | Mặc định | Điều kiện đổi |
|---|---|---|
| Voice server | Python 3.12 + Starlette/FastAPI + Uvicorn trong một ASGI process. | Chỉ tách standalone WebSocket nếu benchmark connection/frame path yêu cầu. |
| Manager API | NestJS + Fastify + PostgreSQL + Redis. | Đổi nếu team đã có nền tảng Java/Spring bắt buộc. |
| Manager Web | Vue 3 + TypeScript + Vite + TanStack Vue Query + Zod. | Giữ visual prototype hiện tại, bổ sung artifact/security/privacy screens. |
| Device edge | Caddy/Nginx listener 8003 proxy device routes tới manager-api/object store. | Có thể gộp vào manager-api nếu vẫn giữ canonical routes/fixtures. |
| Object storage | Local filesystem adapter cho dev; MinIO cho rollout/Range/multi-node. | Không để manager-api buffer artifact lớn trong RAM. |
| Resource layout | Executable A/B + resource A/B sau size probe; ưu tiên đơn giản/recover cho V1. | Mở ADR resource store 8 MB nếu model/assets vượt slot. |
| Manifest signature | RFC 8785 JCS + detached Ed25519 là target; crypto spike trên ESP-IDF là gate. | Nếu spike fail, ADR chuyển sang primitive hỗ trợ ổn định hơn. |
| VAD/admission | ESP AFE cho capture/wake; `silero-local` trên voice-server cho VAD/endpoint; admission là gate tổng quát sau ASR. | Chọn thêm denoise/AEC/target-speaker theo board và benchmark. |
| ASR | Zipformer Vietnamese 30M INT8 primary; ChunkFormer-CTC-Large-Vie fallback có điều kiện. | Có thể tạm Zipformer-only trong bring-up nếu server chưa đủ tài nguyên. |
| LLM | `openai-compatible-9router` cho dev/LAN; adapter giữ tương thích Chat Completions/Responses, SSE, structured output, tool calling và cancellation. | Chuyển official API/self-hosted nếu 9router không đạt contract hoặc không phù hợp quyền sử dụng. |
| TTS | VieNeu-TTS v3 Turbo local primary `vi-VN`, sentence/stream chunk theo capability probe. | Thêm local/cloud fallback chỉ sau license, privacy và latency benchmark. |
| Tenant | Schema tenant-aware, UI V1 một workspace/owner. | Mở full tenant/RBAC sau khi voice loop ổn định. |
| LAN security | HTTP/WS chỉ cho dev LAN; release LAN dùng HTTPS/WSS với local CA/SPKI pinning. | Public cần tunnel/domain/TLS. |
| Config apply | Sensitivity/cooldown ở standby; model pack stage inactive slot và restart subsystem; firmware/apply ở safe boundary. | Chỉ hot reload nếu capability/health test chứng minh an toàn. |

## 3. Bắt buộc xác nhận trước Phase 0

### Hardware

1. Pin map thật và schematic module N16R8.
2. ST7789 resolution, rotation, RGB/BGR, inversion, offset và SPI speed.
3. INMP441 L/R slot, noise floor, clipping, gain và sample clock.
4. MAX98357A nguồn, gain, loa, volume curve và khoảng cách mic–loa.
5. Robot luôn cắm điện hay có pin/deep sleep.
6. Ảnh wiring/vỏ robot để quyết định wake/AEC benchmark.

### Runtime và resource

1. Chấp thuận ESP-SR WakeNet/MultiNet cho V1.
2. Scope asset V1: `srmodels.bin`, font Việt, icon, chime hay thêm animation/GIF.
3. Chọn resource A/B V1 hay chấp nhận ADR resource store 8 MB nếu size probe vượt slot.
4. Chấp thuận model pack không cho upload native runtime/operator.
5. Xác nhận custom wake V1 chỉ upload/chọn model đã build; training service để phase sau.

### AI/provider

1. Xác nhận máy chạy voice-server có CPU/RAM/VRAM/GPU và số session đồng thời mục tiêu.
2. 9Router đã pass local smoke cho Chat Completions/Responses, SSE và forced tool call; còn xác nhận cancellation/soak/quota và auth policy trước khi freeze.
3. Ghi exact repository/commit/runtime format/license của Zipformer, ChunkFormer, Silero và VieNeu.
4. Xác nhận VieNeu streaming hay batch, voice/profile `vi-VN`, sample rate và output format.
5. Chấp thuận bật ChunkFormer fallback ngay V1 hay chỉ sau benchmark Zipformer.
6. Model smoke-test `cx/gpt-5.4-mini` đã chọn tạm với `reasoning_effort=none`; còn xác nhận context/chi phí/quota và model fallback nếu 9router có nhiều model. Không dùng credential phiên Codex trực tiếp.
7. Có cần voiceprint/target-speaker ngay V1 không; mặc định là không.
8. Transcript có lưu không, retention bao lâu, có consent/opt-out thế nào.

### Conversation UX

1. Default timeouts: 15 s first input, 30 s between turns, 5 s closing grace, 20 s max utterance, 10 phút max session.
2. Khi input unclear: hỏi lại một lần hay bỏ qua im lặng.
3. Khi user nói “dừng lại” lúc TTS: chấp nhận best-effort V1 hay chờ AEC trước khi quảng bá.
4. Tool nào AI được tự gọi, tool nào cần confirmation.
5. Có cho phép follow-up không cần lặp wake word trong cùng assistant gate không; mặc định là có.

### Backend/deployment

1. Có chấp thuận NestJS + Python dual-stack không.
2. Port 8003 do Caddy/device-edge hay manager-api trực tiếp phục vụ.
3. Dev dùng local filesystem hay MinIO ngay từ đầu.
4. Single-node V1 đã chốt; model/provider gọi qua loopback và 9Router phải bind `127.0.0.1` hoặc firewall chặn port `20128` khỏi LAN.
5. V1 single workspace hay cần UI multi-tenant/RBAC hoàn chỉnh.
6. Dev LAN HTTP/WS và release LAN pinned HTTPS/WSS có chấp thuận không.

### UI/UX

1. Visual/interaction prototype đã được duyệt, không cần xác nhận lại; chỉ còn xác nhận phạm vi màn hình bổ sung.
2. Có cần mobile manager ngay V1 không.
3. Màn hình bắt buộc V1: Overview, Devices, Pairing, Agent, Providers, Realtime Lab, MCP, OTA, Wake/Resources, Audit/Privacy.
4. Có hiển thị raw transcript/audio trong Realtime Lab không; mặc định chỉ hiển thị redacted events.
5. Có cần live device animation/camera trong Manager Web không; mặc định để phase sau.

### Security/legal

1. License code mới đã chốt MIT; mọi code port/derive từ Xiaozhi hoặc dependency khác phải giữ NOTICE/copyright tương ứng.
2. Crypto spike chấp thuận JCS + Ed25519 target và fallback ADR.
3. Secure Boot/Flash Encryption bật ở production release hay chỉ sau pilot.
4. Remote MCP endpoint nào được allowlist; policy SSRF/egress.
5. Provider terms/license có cho phép lưu transcript, model output và TTS audio không.

## 4. Gate trước khi giao AI code

Không giao task implementation lớn nếu chưa có các artifact sau:

- Board README đã freeze pin map và ảnh wiring.
- Contract JSON Schema + fixtures canonical và compatibility alias.
- Provider baseline fixture `fixtures/config/provider-baseline-v1.json` và capability/conformance report của từng model.
- Conversation policy fixture không còn `maxProviderSeconds`/source-specific heuristic.
- ESP-SR model-pack fixture và capability fixture.
- Partition table/size report trên firmware build thật.
- Signed manifest test vector và crypto verification test.
- Decision về button guarantee/AEC speaking interrupt.
- Provider baseline có credential test và latency budget.
- UI sitemap/wireframe đã duyệt cho các màn V1.
- Privacy/retention/license/security profile đã ghi thành ADR.
