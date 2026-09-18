# VeeTee Production Readiness — 2026-09-18

## Trạng thái

**Source/runtime hardening: READY cho local/trusted-LAN deployment.**

Service production hiện được quản lý bằng systemd, HTTP/API/Web dashboard và WebSocket có thể khởi động ở trạng thái `degraded` khi LLM credential thiếu/sai thay vì làm toàn bộ process chết. Trạng thái LLM hiện tại chưa được chứng nhận `ready` vì deployment local chưa có `GROQ_API_KEY_*` hợp lệ.

Không diễn giải `liveness=alive` thành AI-ready. Consumer phải đọc cả `status`, `readiness` và `degraded_reasons`.

## Security / access — DONE

- Device WebSocket dùng credential riêng theo device, không còn shared WS token.
- OTA credential được phát **một lần** sau pairing/activation và không bị redisclose chỉ dựa trên `Device-Id` + `Client-Id`.
- Re-pair tạo/rotate credential mới.
- Pending pairing có TTL, global cap, per-source/device create-rate limit và giới hạn chiều dài identity/metadata.
- Management API/session yêu cầu management token; browser nhận HttpOnly + SameSite=Strict cookie.
- Origin policy áp dụng cho browser WebSocket.
- HTTP body có giới hạn 64 KiB.
- Response có CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy và Permissions-Policy.
- Secret runtime được mask trong API/dashboard.
- `.env`, `config.yaml`, manager state và SQLite local đều bị Git ignore; real secret không nằm trong commit.
- Local secret/state permission hiện là `0600`, thư mục data `0700`, owner thống nhất `quangvu`.
- Conversation/ASR/LLM/music/cache logging chính không ghi nguyên văn nội dung ở INFO/WARN; log dùng chars/status/latency/ID.

## Runtime / reliability — DONE

- Thiếu hoặc sai Groq credential không còn chặn management plane khởi động.
- `UnavailableLLM` fail-closed giữ HTTP/dashboard/OTA hoạt động để admin có thể sửa cấu hình.
- Permanent LLM errors (ví dụ credential/config invalid) không bị retry vô hạn.
- `/health` trả `status=degraded` khi readiness chưa đạt, đồng thời giữ `liveness=alive`.
- Parakeet/TTS/provider failure được phản ánh qua readiness thay vì giả vờ AI-ready.
- systemd unit được verify, enable và chạy non-root bằng user `quangvu`.
- systemd có restart-on-failure, UMask 0077 và sandbox/hardening flags.
- Vite dev server không dùng cho production; production static được serve từ `:8003`.

Live evidence sau restart 2026-09-18:

```text
service: active
WebSocket: :8000 LISTEN
HTTP/API/Web: :8003 LISTEN
health.status: degraded
health.liveness: alive
health.readiness: degraded
degraded_reasons: [llm_not_warm]
LLM warmup retry loop after permanent configuration failure: 0
```

## Memory isolation — DONE ở source, acceptance còn phụ thuộc deployment

- Paired device có `owner_id` riêng.
- Session authenticated bằng device credential dùng device owner cho durable memory.
- `memory.trusted_owner_id` chỉ còn fallback cho deployment legacy/single-owner.
- Project default vẫn giữ `durable_enabled=false` để fail-safe.
- Local ignored config hiện bật durable memory và global owner nhưng chưa có paired device, nên chưa có dữ liệu device-owner để migrate/test chéo owner trên máy này.

Khi deployment có nhiều owner, phải gán `owner_id` cho từng device và chạy acceptance isolation/restart/forget trước khi coi multi-owner memory là certified.

## Frontend / dashboard — DONE

- Dashboard Vue/Vite đã được redesign hoàn toàn theo workspace desktop, Be Vietnam Pro self-hosted.
- Không còn Google Fonts runtime dependency.
- Legacy CSS layer đã được loại bỏ; còn một design system chính trong `web/src/app.css`.
- Management login, Assistants, Devices/Pairing, Runtime, Mission Control và Voice Console dùng reusable UI components.
- Health badge phản ánh degraded state.
- Voice Console dùng AudioWorklet-first; ScriptProcessor chỉ là fallback cho browser cũ.
- Opus V1/V2/V3 framing + truncation và PCM resampling được tách sang `web/src/lib/voiceAudio.js` và có unit test.

## Verification evidence

Backend:

```text
282 / 282 unittest PASS
latest full run: 4.694s
compileall: PASS
pip check: No broken requirements found
```

Frontend:

```text
12 / 12 Vitest PASS
5 test files
Vite production build: PASS
npm audit --audit-level=high: 0 vulnerabilities
```

Release/Git:

```text
git diff --check: PASS
staged secret scan: 0 actual findings
sensitive local paths: ignored and untracked
```

Release commits:

```text
1098077 Harden VeeTee runtime and rebuild dashboard
fc0d39d Extract tested voice audio helpers
```

Các commit đang local trên branch `work/production-hardening-20260916`; không tự push remote.

## Việc còn phụ thuộc external acceptance

### 1. Groq credential

Cần cấu hình ít nhất một `GROQ_API_KEY_<alias>` hợp lệ trong `.env` hoặc management Runtime UI, sau đó restart/reload theo flow vận hành. Không đưa secret vào YAML/Git.

Chỉ sau đó mới chạy lại live semantic/chat/tool/latency certification. Không dùng key placeholder hoặc key cũ trả 401 làm evidence.

### 2. TLS khi expose ra ngoài trusted LAN

Current service bind LAN để ESP32 stock có thể kết nối. Nếu endpoint được expose qua Internet hoặc mạng không tin cậy, phải đặt HTTPS/WSS reverse proxy/domain/certificate phía trước và giữ origin policy hẹp.

Không tự bật cookie Secure trong backend HTTP-only local vì điều đó sẽ làm browser LAN HTTP không đăng nhập được; Secure cookie phải đi cùng deployment TLS thật.

### 3. Hardware acceptance

Unit/integration test không thay thế ESP32 vật lý. Cần board stock thật để chứng nhận:

- OTA/pair/reboot/reconnect với one-time credential;
- mic → ASR → LLM → TTS → loa nhiều lượt;
- V1/V2/V3 nếu firmware/hardware target dùng;
- interrupt/listen:start/abort behavior;
- idle close/re-wake;
- audio tail;
- AEC / automatic barge-in nếu muốn bật;
- latency/failure-rate trong network thật.

### 4. Semantic corpus / performance certification

Corpus human-review, held-out critical set, benchmark 100-attempt và p95 hardware vẫn là acceptance work. Không nâng historical benchmark thành chứng nhận cho code/runtime 2026-09-18 nếu chưa đo lại.

## Maintainability debt được giữ có chủ đích

`core/session.py` vẫn lớn vì đang giữ realtime state machine đã có regression coverage dày. Không rewrite toàn bộ chỉ để giảm số dòng trong production-hardening pass. Nên tiếp tục extraction theo kiểu pure helper/sub-controller nhỏ, mỗi lần kèm test, tương tự cách browser voice audio vừa được tách.

Broad exception handlers ở provider/process/cleanup/HTTP boundary cũng không được xóa cơ học. Chỉ thu hẹp khi có failure contract rõ ràng và regression test tương ứng.

## Release rule

Một release mới chỉ nên được gọi là **AI-ready** khi đồng thời:

1. systemd active;
2. `/health` có `status=ready`, `readiness=ready`;
3. không có permanent provider error trong journal;
4. backend + frontend tests/build/audit/diff-check xanh;
5. nếu thay protocol/audio/auth: hardware smoke tương ứng xanh;
6. real secrets không xuất hiện trong staged diff.
