# Xiaozhi capability inventory cho Veetee

> Đây là tài liệu tham khảo và ma trận capability, không phải plan task, backlog
> hay cam kết thứ tự triển khai. Mục đích là ghi lại những chức năng hữu ích tìm
> thấy khi đối chiếu hai source Xiaozhi read-only với Veetee hiện tại.
>
> Audit source: 2026-07-23. Hai thư mục `references/xiaozhi-esp32` và
> `references/xiaozhi-esp32-server` chỉ được đọc, không chỉnh sửa.

## 1. Kết luận ngắn

Veetee đã có phần lớn luồng lõi: provisioning, activation, hội thoại `mode=auto`,
admission theo ngữ cảnh, cancellation/deadline, assistant versioning, Realtime Lab,
MCP thiết bị và signed resource/UI A/B. Không nên lấy số lượng module Xiaozhi làm
đích đến hoặc port nguyên repository.

Các khoảng trống giá trị cao nhất là:

1. signed executable firmware OTA và rollout vận hành;
2. diagnostic/self-test audio, network và thiết bị;
3. benchmark production cho wake, ASR, TTS, LLM, admission và end-to-end;
4. AEC/full-duplex/voice barge-in trên phần cứng thật;
5. memory qua phiên, Knowledge Base/RAG;
6. remote/server MCP và các tool tích hợp có policy;
7. voice catalog, dynamic ASR/TTS và correction lexicon.

Các mục camera, voiceprint/voice clone, MQTT+UDP, digital human, mobile app và
toàn bộ board/provider matrix nên để sau hoặc ngoài scope hiện tại.

## 2. Quy ước trạng thái và ưu tiên

| Ký hiệu | Ý nghĩa |
|---|---|
| ✅ | Veetee đã có tương đương hoặc tốt hơn |
| 🟡 | Đã có nền tảng nhưng chưa hoàn chỉnh |
| ❌ | Chưa có implementation thực tế |
| 🧪 | Phải benchmark/test trên phần cứng |
| 🚫 | Không nên port |
| P0 | Release blocker hoặc cần xác nhận sớm |
| P1 | Giá trị cao, nên xem xét tiếp |
| P2 | Làm sau khi nền tảng ổn định |
| P3 | Tùy chọn hoặc phụ thuộc định hướng sản phẩm |

Các nhãn P0-P3 trong tài liệu này chỉ biểu thị mức giá trị, rủi ro và phụ thuộc
kỹ thuật tại thời điểm audit. Chúng không tự tạo task, milestone hoặc lịch triển
khai.

## 3. Những phần Veetee đã có, không cần port lại

| Capability | Trạng thái | Bằng chứng/nhận xét |
|---|---:|---|
| Captive portal, nhiều Wi-Fi profile, AP fallback | ✅ | Luồng Veetee đã có và đã bring-up trên board. |
| Activation, pairing code/challenge và physical recovery | ✅ | Đã có challenge, code 6 số, token và recovery bằng physical hold. |
| Hội thoại `mode=auto` | ✅ | VAD tự kết thúc lượt, không cần nhấn nút lần hai. |
| Admission trước LLM/MCP | ✅ | Có `accepted`, `unclear`, `not_addressed`, `non_actionable`, follow-up và social response. |
| Context bounded trong phiên | ✅ | `ConversationEngine` truyền context có giới hạn số message/ký tự. |
| Cancellation/deadline | ✅ | Turn, admission, planner, LLM, TTS, MCP, inactivity và closing grace có scope riêng. |
| Assistant/version/device assignment | ✅ | Immutable assistant config version và device desired/reported state. |
| LLM failover/circuit breaker | ✅/🟡 | LLM chain đã có; ASR/TTS chưa dynamic chain đầy đủ. |
| MCP thiết bị | ✅ | JSON-RPC, schema/range validation, pagination, confirmation, safety class và audit. |
| Signed resource/UI A/B | ✅/🧪 | Manifest, inactive slot, health và rollback đã có; power-loss/LCD acceptance còn thiếu. |
| Manager Web | ✅ | Có Overview, Devices, Agents, Providers, Realtime Lab, Resources và Operations. |
| Local/LAN/Tailscale không domain | ✅ | Endpoint vẫn configurable qua bootstrap; không cần mua domain. |

## 4. Firmware và chẩn đoán thiết bị

### 4.1. Audio debugger có kiểm soát — ❌ P0

Xiaozhi có `AudioDebugger` gửi PCM thô qua UDP. Giá trị chính là nhìn được mic
noise floor, clipping, frame drop và dữ liệu thực tế khi ASR sai.

Veetee nên học mục đích chẩn đoán, không bật raw UDP mặc định:

- diagnostic session có thời hạn và user/physical confirmation;
- LAN/Tailscale destination allowlist;
- tự tắt sau một số giây;
- RMS, peak, noise floor, clipping và frame-drop metrics;
- playback underrun, Opus error, queue high-water mark;
- tùy chọn tải sample 3-5 giây, không lưu audio mặc định;
- redact token, Wi-Fi password, activation secret và transcript.

### 4.2. Self-test từ Manager — ❌ P0

Nên có một bài test có kết quả pass/fail thay vì nhiều thao tác rời rạc:

- mic 5 giây: signal, DC offset, clipping, silence;
- speaker tone/sweep;
- button, LED, LCD, PSRAM, flash và partition;
- Wi-Fi RSSI/DNS/TCP tới Manager/Voice Server;
- WebSocket hello, uplink Opus, downlink Opus;
- báo cáo kết quả và failure reason về Manager.

Self-test không được đổi Wi-Fi profile hiện tại hoặc xóa NVS.

### 4.3. Device health/system information — 🟡 P0

Xiaozhi expose flash size, free/min heap, task list, CPU runtime và power lock.
Veetee MCP hiện mới có board, firmware, state và volume. Nên bổ sung:

- reset reason, uptime, firmware/resource/UI slot;
- free/min internal RAM và PSRAM;
- Wi-Fi RSSI, IP, gateway, reconnect count, disconnect reason;
- bootstrap/WebSocket failure reason và retry count;
- audio/wake queue drop;
- chip temperature nếu target hỗ trợ;
- coredump marker, boot-loop counter;
- health bundle có redaction.

### 4.4. Nhiều board/codec — 🚫

Không port hàng loạt board, codec ES8311/ES8388, cellular/4G hoặc profile camera.
Veetee V1 cố ý giữ một board ESP32-S3 N16R8; chỉ tách abstraction khi board revision
thật sự yêu cầu codec hoặc pin map khác.

### 4.5. Battery, charging và LED animation — P3

Chỉ làm nếu board thật có pin/PMIC/LED strip:

- battery percentage, charging, low-battery;
- sleep/power-save;
- LED pattern theo state.

Không suy ra capability này chỉ vì source Xiaozhi có nhiều board hỗ trợ.

## 5. Audio realtime, wake word và AEC

### 5.1. AFE/AEC/NS/AGC — ❌ P1 + 🧪

Xiaozhi có AFE engine và AEC khi có playback reference. Veetee chưa có far-end
reference/AEC production, vì vậy voice interrupt lúc loa đang phát chỉ là
best-effort.

Thứ tự hợp lý:

1. đưa playback reference vào audio graph;
2. đo ERLE, false VAD, clipping và latency;
3. thêm NS/AGC nếu không phá wake/ASR;
4. test nhiều volume/khoảng cách;
5. chỉ quảng bá full-duplex sau khi gate đạt.

### 5.2. Wake-word audio pre-roll/cache — ❌ P1

Xiaozhi có ring buffer PCM trước thời điểm wake. Veetee nên:

- giữ buffer ngắn trong PSRAM;
- chuyển phần hậu wake cần thiết vào ASR;
- không gửi toàn bộ wake phrase nếu privacy policy không cho phép;
- dùng generation guard chống audio từ wake cũ;
- đo wake-to-first-uplink-frame và sample bị mất.

### 5.3. Production `Hey VeeTee` — 🟡 P0/P1 + 🧪

Runtime/model-pack đã có, nhưng model tiếng Việt production chưa pass corpus.
Corpus cần bao gồm:

- nhiều giọng Việt, vùng miền, giới tính, khoảng cách;
- quạt, TV, nhạc, tiếng nói nền, near-confusion;
- false accept/hour và false reject;
- thiết bị đang phát TTS;
- version model/corpus/threshold trong artifact metadata;
- stable rollout chỉ sau benchmark pass.

### 5.4. Voice barge-in khi đang nói — ❌ P1 + 🧪

- local interrupt profile phải hoạt động lúc `thinking`;
- khi `speaking`, cần AEC/far-end reference;
- semantic/free-form interrupt đi qua ASR/admission;
- button vẫn là abort guarantee;
- kiểm tra stale TTS packet sau abort và abort-to-silence.

### 5.5. Offline earcon và activation prompt — 🟡 P1

Xiaozhi có OGG demuxer, startup/success/error/low-battery sound và audio chữ số.
Veetee nên phân phối dưới dạng signed `audio_assets`:

- Wi-Fi configuration started/saved/failed;
- activation code digits hoặc prompt offline;
- connected/disconnected;
- provider unavailable;
- update started/completed/rolled back;
- low battery nếu board có pin.

Không compile phrase/locale vào application logic.

### 5.6. Server AEC timestamp V2 — ❌ P2

Có thể bổ sung sau bằng versioned protocol:

- timestamp capture/playback;
- sequence/loss accounting;
- playback ACK;
- server-side echo alignment.

Không phá WebSocket V1 hiện tại.

### 5.7. Native realtime provider — ❌ P2

Manager đã có provider kind `realtime`, nhưng runtime chưa có adapter hoàn chỉnh.
Chỉ làm sau khi duplex/AEC, event mapping, cancellation và cascade fallback ổn định.

## 6. Network, transport và OTA

### 6.1. Signed executable firmware OTA — ❌ P0

Manager đã trả metadata `firmware.version/url`, nhưng đây chưa phải firmware OTA
end-to-end. Cần:

- immutable firmware release;
- board/chip/version/security-epoch compatibility;
- SHA-256 và detached signature;
- inactive executable slot;
- image verification trước boot;
- pending verify, health window, mark-valid;
- bootloader rollback;
- không update giữa conversation;
- progress và failure reason;
- rollback chỉ tới image đã ký/policy-approved.

### 6.2. Canary/percentage/pause/resume/rollback — 🟡 P0/P1

Resource/UI rollout hiện chủ yếu explicit-device. Cần bổ sung:

- dev/canary/stable channel;
- percentage rollout;
- pause/resume;
- auto-pause theo health threshold;
- operator rollback;
- desired/downloaded/staged/pending-health/active/failed/rolled-back;
- rollback firmware, wake resource và UI Pack độc lập.

`publish` hoặc `desired` không được hiển thị như `active`.

### 6.3. Resumable artifact download — 🟡 P1

- HTTP Range resume;
- download journal;
- content length/hash trước apply;
- bounded retry/backoff;
- xóa inactive corrupt payload;
- power-loss test tại nhiều offset.

### 6.4. MQTT + encrypted UDP — ❌ P2

Chỉ thêm nếu benchmark chứng minh cần cho connection scale, gateway fan-out hoặc
latency. WebSocket vẫn là transport native mặc định. Không để bootstrap tự chuyển
transport chỉ vì response có thêm MQTT object.

### 6.5. BluFi/acoustic provisioning — P3

Captive portal Veetee đã là đường mặc định. Chỉ cân nhắc BluFi nếu portal tiếp tục
có tỷ lệ thất bại thực tế cao; acoustic provisioning không nên thêm sớm.

### 6.6. TLS local/Tailscale — 🟡 P1

Không cần domain:

- Tailscale IP/DNS hoặc Tailscale HTTPS;
- LAN local CA với IP SAN/SPKI pinning;
- HTTPS/WSS cho token và artifact;
- endpoint configurable qua bootstrap.

## 7. Device UI và asset

### 7.1. Rich UI Pack — 🟡 P1/P2

Xiaozhi có LVGL theme, image, JPEG, GIF, emoji và dynamic glyph. Veetee đã có
UI Pack A/B an toàn hơn nhưng ABI còn giới hạn. Có thể mở rộng data-only:

- bitmap/background;
- icon atlas;
- font/glyph subset;
- animation giới hạn frame/size;
- text style/layout constraints;
- RAM/flash budget;
- smoke render inactive slot;
- built-in Signal failsafe.

### 7.2. Locale UI/offline prompt — 🟡 P2

UI Pack hiện kiểm tra `vi-VN`. Nên chuẩn bị:

- fallback `vi-VN -> vi -> default`;
- font subset theo locale;
- offline string/earcon theo locale;
- locale chỉ mở sau khi provider tương ứng benchmark pass.

### 7.3. Biểu cảm và state animation — P2

Có thể thêm standby/listening/thinking/speaking/error/updating dưới dạng data-driven
state. UI Pack không được thay state machine, admission, provider routing hoặc MCP
permission.

### 7.4. Digital human/Live2D — P3

Xiaozhi có web digital-human, nhưng Realtime Lab của Veetee đã đủ vai trò thiết bị
ảo. Live2D chỉ là lớp trình bày tùy chọn, không phải voice capability lõi.

### 7.5. Camera/vision — P3 + 🧪

Chỉ làm khi camera hardware, privacy indicator, upload limit và VLM provider đã
được chốt. Không thêm camera vào firmware hiện tại chỉ vì source tham khảo có.

## 8. Provider AI và benchmark

### 8.1. Dynamic ASR chain — 🟡 P1

Veetee đã có provider records cho ASR nhưng runtime session profile hiện mới
resolve LLM chain đầy đủ. Nên thêm:

- Zipformer local primary;
- một fallback được benchmark;
- capability streaming/locale/timestamps/confidence;
- retry chỉ khi chưa finalize và còn deadline;
- không fallback cloud trong local-only privacy profile.

Không port toàn bộ Aliyun/Baidu/Tencent/Doubao/Xunfei.

### 8.2. Dynamic TTS chain và voice catalog — 🟡 P1

- VieNeu local primary;
- một fallback TTS;
- voice ID, locale, sample rate, streaming/batch;
- preview giọng trong Manager;
- speed/sample-rate capability;
- cache chỉ theo privacy policy.

### 8.3. LLM adapter mở rộng — 🟡 P2

LLM failover hiện đã có. Chỉ thêm direct OpenAI-compatible, Ollama, Gemini hoặc
provider khác khi có nhu cầu và từng adapter pass structured output/tool-calling/
cancellation conformance.

### 8.4. Performance tester/conformance dashboard — ❌ P0

Nên có benchmark artifact/version cho:

- ASR: WER/CER, final latency, stability, noise;
- TTS: TTFA, RTF, pronunciation, cancellation;
- LLM: TTFT, tokens/s, structured output, tool call, abort;
- admission: false accept/reject;
- wake: FAR/FRR và media noise;
- end-to-end: wake-to-first-audio, abort-to-silence.

### 8.5. Provider health/circuit — 🟡 P1

Mở rộng readiness/circuit hiện có cho VAD, ASR, TTS, realtime và memory:

- last success/error;
- p50/p95 latency;
- open/half-open/closed;
- error budget;
- secret redaction.

### 8.6. Cache/GC/output pacing — P2

Chỉ học ý tưởng từ Xiaozhi sau load test: bounded cache, TTS pacing, output cap và
memory-pressure metrics. Không thêm GC thủ công nếu chưa có số liệu.

## 9. Memory, lịch sử và RAG

### 9.1. Context trong phiên — ✅

Veetee đã giữ bounded context theo số message và số ký tự; đây là nền tảng cho
follow-up như “Gke vậy sao?”.

### 9.2. Short-term memory qua nhiều phiên — ❌ P1

Bắt đầu nhỏ và có consent:

- structured facts, không blob prompt;
- confidence/source/timestamp/expiry;
- xem/sửa/xóa;
- bounded size và retention;
- không lưu raw audio;
- không tự gửi dữ liệu nhạy cảm ra cloud.

Không copy nguyên prompt/scoring cứng của reference.

### 9.3. Chat history/title/summary — ❌ P2

Nếu bật, phải có opt-in, redaction, retention 1-30 ngày, delete/export và audio
retention riêng.

### 9.4. Knowledge Base/RAG — ❌ P1

Đây là capability đáng làm:

- upload PDF/TXT/Markdown;
- type/size/virus validation;
- parser/chunker versioned;
- embedding/index job;
- scope theo assistant;
- retrieval deadline/top-k cap;
- citation/source;
- re-index/disable/delete;
- không đưa Manager API vào frame-by-frame audio path.

### 9.5. Mem0/PowerMem/vector memory — P2/P3

Chỉ làm sau bounded local memory, với tenant isolation, encryption, consent, delete
và benchmark relevance.

### 9.6. Context provider — P1

Structured provider cho current time/timezone, device state, assistant version,
optional location và recent tool result.

### 9.7. ASR correction dictionary — P1

Dùng cho tên riêng, từ chuyên ngành và pronunciation lexicon theo assistant/locale.
Không dùng để hard-code semantic intent hoặc sửa mọi câu bằng exact string.

### 9.8. Voiceprint/target speaker — P2

Chỉ opt-in, có consent, encryption, delete/re-enroll và benchmark nhiều noise/mic.
Đây là một admission signal, không phải điều kiện duy nhất để AI trả lời.

## 10. MCP, tool và tích hợp

### 10.1. Device MCP — ✅

Veetee hiện có device status, volume, system info, JSON-RPC, pagination, schema
validation, confirmation, safety class và audit.

### 10.2. Remote MCP endpoint registry — ❌ P1

Nên có registry trong Manager:

- endpoint URL/transport/auth reference;
- tenant/assistant scope;
- tool allowlist;
- health probe;
- timeout/cancellation;
- confirmation theo safety class;
- audit và result size cap;
- không arbitrary executable code.

### 10.3. Server MCP registry — ❌ P1

Tách server tools khỏi device tools, có namespace, version/capability, policy,
deadline, concurrency limit và structured result.

### 10.4. Time/weather/web search — ❌ P1

Các tool có giá trị cao nếu làm schema-driven:

- `context.get_time`;
- `weather.current`;
- `search.web`.

Location phải lấy từ config hoặc hỏi người dùng; kết quả nên có source/citation và
cache TTL. Không copy API riêng của reference.

### 10.5. Home Assistant — ❌ P1/P2

Read state có thể không cần confirmation; set state/play media cần allowlist và
confirmation tùy safety class. Không cho LLM tự điều khiển actuator nguy hiểm.

### 10.6. RAG search — ❌ P1

Triển khai sau Knowledge Base, có assistant scope, citation, timeout và không bịa
kết quả khi retrieval fail.

### 10.7. Music/news — ❌ P2

Cần content source hợp pháp, streaming/cancellation, volume ducking, license và
cache policy.

### 10.8. Call device/address book — P3 hoặc 🚫

Chỉ làm khi Veetee có scope telephony rõ ràng.

### 10.9. Persona/role change bằng tool — 🚫

Tiếp tục dùng immutable assistant version; không để câu nói thay đổi persona lâu
dài một cách âm thầm.

### 10.10. Arbitrary Python plugin loader — 🚫

Thay bằng server release đã ký hoặc remote MCP có policy. Không đưa executable
plugin vào resource bundle.

## 11. Manager API/Web

| Module | Trạng thái | Ưu tiên | Nội dung tham khảo |
|---|---:|---:|---|
| Firmware Releases | ❌ | P0 | Sign/publish/compatibility/channel/rollout/rollback. |
| Benchmark Center | ❌ | P0 | Wake/ASR/TTS/LLM/admission/end-to-end corpus và gate. |
| Rollout Control | 🟡 | P0/P1 | Percentage, pause/resume, auto-pause, operator rollback. |
| Device Diagnostics | ❌ | P0 | Self-test, metrics, health bundle, failure reason. |
| Knowledge Base | ❌ | P1 | Upload, indexing, assistant assignment, citation test. |
| Remote MCP Registry | ❌ | P1 | Endpoint, auth, allowlist, health, audit. |
| Assistant Template | ❌ | P1 | Tạo assistant từ template/version đã duyệt. |
| Voice Catalog | ❌ | P1 | Preview, locale, provider, speed/sample-rate capability. |
| Correction Dictionary | ❌ | P1 | Lexicon version theo assistant/locale. |
| Feature/Policy Flags | 🟡 | P1 | Immutable policy version, không boolean toàn cục tùy tiện. |
| Chat History/Summary | ❌ | P2 | Opt-in, redact, retention, delete/export. |
| Voice Clone | ❌ | P2 | Consent, sample validation, provider job, revoke/delete. |
| Voiceprint | ❌ | P2 | Enrollment, threshold, consent, retention. |
| User/RBAC UI | 🟡 | P2 | Backend có auth/role nhưng UI còn tối giản. |
| Tags/filter fleet | ❌ | P2 | Tag assistant/device, bulk selection an toàn. |
| Mobile app riêng | ❌ | P3/🚫 | Web responsive đã phù hợp local/Tailscale. |
| SMS/register/password recovery | ❌ | 🚫 hiện tại | Không cần cho local single-owner deployment. |
| Address book | ❌ | 🚫 hiện tại | Chưa có scope telephony. |

## 12. Security, operations và hardware release

### 12.1. Hardware release matrix — P0 + 🧪

Cần kiểm thử trước khi gọi production-ready:

- blank flash -> AP -> Wi-Fi -> activation;
- sai password, router reboot, Manager/Voice Server down;
- mic silence/clipping/noise và speaker volume;
- abort trong ASR/LLM/MCP/TTS;
- power loss khi download/stage/apply;
- corrupt manifest/payload/signature;
- pending-health và boot rollback;
- wake/UI resource rollback độc lập;
- firmware OTA rollback;
- heap/PSRAM/WS/audio soak tối thiểu 10 phút.

### 12.2. Signer, provenance, SBOM — P1

Firmware/resource/UI nên có signer tách key, key rotation, security epoch, provenance,
SBOM/license manifest và revocation. Private signing key không nằm trong repo hoặc
database plain text.

### 12.3. Secret management — P1

Tiếp tục redact log/audit/probe, rotate service token, dùng secret store phù hợp
host và không đưa API key vào firmware.

### 12.4. Observability — P1/P2

Mở rộng telemetry/audit hiện có với trace theo session/turn/generation, p95
wake-to-first-frame, STT final, TTFT, TTFA, abort-to-silence, provider/tool timeout,
artifact rollback, Wi-Fi reconnect/reset và backup/restore.

### 12.5. Privacy — P1 trước memory/voiceprint

Raw audio không lưu mặc định. Transcript, memory, voiceprint, voice clone và
diagnostic session phải có consent, retention, delete/export và redaction riêng.

## 13. Nhóm không nên port

- Hàng trăm board profile và codec của Xiaozhi.
- Tất cả cloud provider chỉ để tăng số lượng.
- Raw PCM UDP debugger bật mặc định.
- Arbitrary Python/native plugin loader.
- Exact-string intent/persona/locale rules.
- Public registration/SMS/password recovery khi chỉ chạy local/Tailscale.
- Domain bắt buộc.
- MQTT+UDP mặc định trước khi có số liệu scale.
- Camera, battery hoặc cellular khi board chưa có phần cứng tương ứng.
- Persona/role change âm thầm bằng một câu nói.

## 14. Cách dùng tài liệu này

Tài liệu này chỉ là reference để:

- kiểm tra một capability trước khi mở rộng sản phẩm;
- ghi nhận lý do một chức năng bị defer hoặc không port;
- tạo ADR/contract riêng khi thật sự quyết định triển khai;
- đối chiếu với hardware benchmark và provider benchmark;
- tránh hiểu nhầm rằng một module có trong Xiaozhi đồng nghĩa Veetee phải có.

Khi một capability được chọn để triển khai, task riêng phải đọc lại roadmap,
decision register, contract liên quan, ghi rõ observable outcome, test và hardware
gap. Không biến toàn bộ danh sách này thành một task duy nhất.
