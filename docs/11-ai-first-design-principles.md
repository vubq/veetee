# Nguyên tắc AI-first cho Veetee

## 1. AI-first nghĩa là gì

Veetee phải hiểu ý định và hành động theo ngữ cảnh, không xây sản phẩm bằng một danh sách câu lệnh cố định. Luồng mặc định là:

```text
Speech -> ASR -> semantic planner/LLM -> response hoặc tool plan
                                      -> policy validation
                                      -> TTS/tool execution
```

Ví dụ, các câu sau đều có thể được hiểu là cùng intent `conversation.end` mà không cần viết từng câu vào firmware:

- “Tạm biệt nhé.”
- “Thôi mình nói chuyện sau.”
- “Veetee ngủ đi.”
- “Bye, hẹn gặp lại.”

Danh sách phrase trong cấu hình chỉ là seed examples cho prompt/test hoặc fast-path model, không phải toàn bộ khả năng hiểu ngôn ngữ của robot.

AI-first không đồng nghĩa với việc giao mọi quyết định cho LLM. Hệ thống phải tách ba lớp:

```text
AI semantic plane       hiểu lời nói, hội thoại, lập kế hoạch và chọn tool
Policy/config plane     quyền, locale, provider, giới hạn và version config
Deterministic runtime   audio, network, state, protocol, security và hardware
```

Trước AI semantic plane phải có `InputAdmissionGate`: đánh giá tổng quát input có hợp lệ, có chủ đích, hướng tới robot và đủ confidence để tạo turn. Không xây một danh sách hard-code kiểu “quạt”, “TV”, “tiếng xe”; đó chỉ là test data của bài toán `non_actionable/not_addressed/low_quality`.

## 2. Những gì không được hard-code theo sản phẩm

Các thành phần sau phải lấy từ agent config/provider registry/manager API hoặc model inference:

- persona, system prompt, giọng nói và phong cách trả lời;
- locale mặc định, fallback locale và quy tắc phát âm;
- ASR/LLM/TTS/realtime provider, model và fallback chain;
- wake profile/model/sensitivity;
- activation wake profile và interrupt profile theo locale;
- model/assets version và capability constraint qua signed resource bundle;
- exit intent và ví dụ ngôn ngữ theo locale;
- input admission/semantic confidence policy;
- conversation inactivity timeout và localized goodbye policy;
- lựa chọn tool dựa trên ngữ nghĩa câu nói;
- tool policy theo tenant/agent/device/role;
- memory policy và retention;
- endpoint voice/OTA/manager nhận qua bootstrap;
- UI text qua i18n resource;
- timeout/cost/quality preference trong giới hạn an toàn cho phép;
- biểu cảm, animation, voice profile và assistant behavior version.

Prompt agent được quản lý như config data có version: runtime chỉ render token
allowlist đã validate, còn persona/tính cách không được biến thành nhánh `if/else`
theo transcript hoặc preset id. Personality chỉ điều chỉnh tone và conversational
stance; safety, authorization, privacy và giới hạn phần cứng vẫn là policy/runtime
deterministic.

Không viết các pattern kiểu:

```cpp
if (text == "tạm biệt" || text == "bye") {
    close_session();
}
```

Thay bằng semantic result có schema:

```json
{
  "intent": "conversation.end",
  "confidence": 0.93,
  "locale": "vi-VN",
  "responseRequired": true
}
```

Intent có thể đến từ LLM structured output, model intent nhỏ hoặc provider realtime. Policy engine chỉ kiểm tra schema, confidence, session state và quyền trước khi thực thi.

## 3. Những gì bắt buộc phải deterministic hoặc cố định

Một robot vật lý không thể loại bỏ hoàn toàn code cố định. Các phần dưới đây bắt buộc phải có behavior xác định để an toàn và có thể phục hồi.

### Hardware contract

- GPIO/I2S/SPI pin map của board thật.
- Driver ST7789, INMP441 và MAX98357A.
- Clock, sample rate, DMA, partition table và PSRAM policy.
- Button debounce và boot strap behavior.

Các giá trị này nên nằm trong board profile/Kconfig thay vì rải trong business logic, nhưng cuối cùng vẫn phải khớp dây nối vật lý. AI không thể tự suy luận và đổi GPIO trong runtime một cách an toàn.

### Wire protocol contract

- WebSocket hello/event names và Opus framing.
- OTA/bootstrap/activation schema.
- MCP JSON-RPC envelope.
- Parser validation, payload limit và protocol version.

Đây là contract tương thích, không phải logic hội thoại. Nếu thay đổi phải version hóa và có migration/fixture.

### Safety và security invariants

- Xác thực token, signature OTA và certificate/public key policy.
- Permission của MCP tool nguy hiểm.
- Giới hạn volume, brightness, motor/actuator và điện áp nếu có.
- Cấm LLM tự factory reset, đổi Wi-Fi, upgrade firmware hoặc gọi URL tùy ý.
- Resource bundle chỉ chứa data/model/assets; executable/runtime change phải qua signed firmware OTA.
- Watchdog, memory limit, queue bound, timeout cứng tối đa và rate limit.
- Recovery khi Wi-Fi/server/model không hoạt động.

LLM có thể đề xuất tool call; deterministic policy engine mới có quyền cho phép và thực thi.

### Conversation runtime invariants

- State machine `standby/listening/thinking/speaking/aborting`.
- Conversation inactivity deadline, closing grace và provider/tool deadline.
- `turn_id`, cancellation generation và drop late event.
- Không phát audio của turn cũ sau abort.
- VAD/audio queue lifecycle.
- Nút interrupt phải hoạt động ngay cả khi AI/provider treo.

Các invariant này phải chạy được khi không có Internet hoặc khi provider lỗi. Không gọi LLM để quyết định có nên tuân thủ `abort` hay không.

## 4. Bốn mức cấu hình

| Mức | Ví dụ | Cách quản lý |
|---|---|---|
| Board compile-time | GPIO, I2S port, partition | board profile/Kconfig, thay đổi cần build firmware |
| Runtime safety | max volume, timeout tối đa, allowed URL schemes | signed config với firmware safe bounds |
| Agent config | prompt, provider, locale, voice, wake profile, tool policy | manager API, immutable version, hot reload cho session mới |
| AI inference | intent, nội dung trả lời, chọn tool, hội thoại tiếp theo | LLM/realtime model với structured output |

Nguyên tắc là chỉ giữ compile-time những gì gắn với phần cứng, recovery hoặc security root. Mọi behavior sản phẩm còn lại phải configurable hoặc do AI suy luận.

## 5. Conversation orchestrator AI-first

Voice server nên có các boundary sau:

```text
TurnArbiter
  quản lý state/cancel/deadline, không hiểu ngôn ngữ

ConversationPlanner
  nhận transcript + context + agent config
  trả structured ConversationPlan

PolicyEngine
  validate plan, tool permission, safety và resource budget

ExecutionEngine
  chạy LLM stream, MCP tools, memory và TTS
```

`ConversationPlan` tối thiểu:

```json
{
  "intent": "knowledge.answer",
  "language": "vi-VN",
  "continueConversation": true,
  "responseMode": "voice",
  "toolCalls": [],
  "emotion": "friendly"
}
```

Nếu model trả output sai schema, hệ thống fallback về một response an toàn và vẫn giữ session hoạt động. Không cho model trả tên class, module Python hoặc GPIO để runtime thực thi trực tiếp.

## 6. Wake word, VAD và exit intent

- Wake word dùng model local nhỏ vì cần latency thấp, privacy và khả năng hoạt động offline. Model/sensitivity/phrase profile có thể OTA/config, không compile phrase vào application logic. ESP32-S3 V1 dùng ESP-SR model pack; runtime/operator mới phải qua signed firmware OTA.
- Button wake và activation wake word là hai input khác nhau nhưng phải mở cùng assistant/session state machine.
- Interrupt profile dùng local model để dừng nhanh khi AI đang xử lý/nói; semantic planner xử lý các cách diễn đạt tự do khi audio/AEC cho phép. Button interrupt luôn deterministic guarantee; voice interrupt khi loa đang phát chỉ được quảng bá sau benchmark AEC.
- VAD là model/signal processing để tìm ranh giới lượt nói; đây là lý do người dùng không cần bấm nút “gửi”.
- VAD không đủ để tạo AI turn. Admission gate còn kiểm tra integrity, relevance, addressed-to-robot và confidence trước LLM/MCP.
- Exit intent được hiểu bằng ngữ nghĩa trên server. Có thể thêm model intent nhỏ làm fast path, nhưng phải trả cùng structured schema với LLM.
- Nếu confidence thấp, robot hỏi lại thay vì đóng phiên nhầm.

## 7. MCP và robot action

AI nhìn thấy tool catalog và tự chọn tool phù hợp. Tuy nhiên mỗi tool phải có schema và policy xác định:

```text
User speech -> AI proposes tools/call
            -> PolicyEngine authorizes arguments/side effect
            -> Tool broker dispatches to firmware/server
            -> Result returns to AI for natural response
```

Ví dụ “nói nhỏ thôi” có thể được AI map sang `self.audio_speaker.set_volume` dựa trên current volume và context. Không cần hard-code toàn bộ các câu “giảm âm lượng”, “nhỏ xuống”, “bé thôi”. Nhưng firmware vẫn phải validate `volume` trong range trước khi chạm hardware.

## 8. Anti-pattern bắt buộc tránh

- So khớp exact string cho intent hội thoại trong firmware.
- Chọn provider bằng chuỗi `if/else` ở conversation core.
- Hard-code prompt, API key, URL hoặc locale trong binary.
- Để LLM tự tạo tool name/URL/GPIO rồi thực thi.
- Dùng LLM cho button interrupt, watchdog, parser hoặc OTA verification.
- Để model output thay đổi state trực tiếp mà không qua schema/policy.
- Tự động ghi nhớ mọi transcript mà không có retention/consent policy.

## 9. Definition of Done AI-first

- Cùng một intent được hiểu qua nhiều cách diễn đạt tiếng Việt, không cần thêm firmware condition.
- Đổi persona/provider/voice/locale không cần build lại firmware.
- Thêm tool mới qua registry/schema/policy, không sửa conversation router trung tâm.
- Model output luôn được validate bằng structured schema.
- Tool nguy hiểm luôn bị deterministic policy chặn hoặc yêu cầu xác nhận.
- Robot vẫn ngắt, recover Wi-Fi, rollback OTA và vào provisioning khi toàn bộ AI provider không hoạt động.
