# Luồng trò chuyện realtime

## 1. Mục tiêu trải nghiệm

`veetee` là robot hội thoại tự nhiên, không phải một thiết bị chỉ trả lời sau khi người dùng bấm nút gửi câu hỏi. Luồng mặc định là:

```text
Mở assistant -> người dùng nói -> VAD nhận ra hết lượt -> AI xử lý
             -> TTS trả lời -> quay lại trạng thái sẵn sàng nghe
```

Các hành vi người dùng cần thấy là:

1. Bấm nút để bật/mở assistant hoặc đánh thức robot; sau đó chỉ cần nói.
2. VAD tự nhận biết điểm kết thúc câu nói và server tự chuyển sang ASR -> LLM -> TTS.
3. Bấm nút khi AI đang suy nghĩ/phát tiếng để ngắt ngay; không cần chờ AI nói xong.
4. Wake word có thể mở assistant từ trạng thái standby/sleep.
5. Exit phrase có thể đưa assistant về standby một cách tự nhiên.
6. Khi có AEC đủ tốt, người dùng có thể chen ngang bằng giọng nói; nếu chưa có AEC, chỉ cam kết nút/wake-word interrupt.

Nút không phải là điều kiện để server gửi câu trả lời. Nút chỉ điều khiển `assistant_gate` (bật/tắt phiên nghe) và `abort` (ngắt turn hiện tại). Chế độ bấm-giữ để thu âm thủ công chỉ là compatibility/accessibility mode, không phải mặc định.

## 2. Hai conversation engine

### Cascade engine (MVP)

```text
Opus frames -> VAD -> streaming ASR interim/final
             -> intent/tool gate
             -> streaming LLM tokens
             -> sentence chunker
             -> streaming TTS chunks -> Opus -> device
```

Ưu điểm là provider đa dạng, dễ dùng Vietnamese ASR/TTS và tool calling. Nhược điểm là mỗi stage có latency/cancellation riêng.

Baseline local tiếng Việt của cascade:

```text
Silero VAD (server local)
  -> Sherpa-ONNX Zipformer Vietnamese 30M INT8 (primary)
  -> ChunkFormer-CTC-Large-Vie (re-decode khi low confidence/unstable)
  -> openai-compatible-9router (streaming LLM candidate)
  -> VieNeu-TTS v3 Turbo (local `vi-VN`)
```

Zipformer và ChunkFormer không chạy đồng thời trên mọi lượt. ChunkFormer cùng
`turn_id` chỉ chạy sau final nếu evaluator thấy confidence/ổn định chưa đạt hoặc
policy yêu cầu chất lượng cao; nếu re-decode không kịp deadline, hệ thống hỏi lại
thay vì gọi LLM với transcript không đáng tin. VieNeu capability phải khai báo
`streaming` hay `batch`; nếu batch, sentence chunker vẫn phát incremental nhưng
không được tính như true streaming.

`9router` là adapter OpenAI-compatible local/dev, không phải ràng buộc conversation
core. Nó phải pass structured output, tool calling, SSE và cancellation conformance;
ChatGPT Plus/Codex login/token không được đưa trực tiếp vào firmware hay provider
secret. Xem `docs/14-model-and-provider-baseline.md` để biết điều kiện freeze.

### Realtime engine (P1)

```text
Opus PCM <-> realtime provider session
          ├─ server tool broker/MCP
          ├─ transcript events
          └─ audio delta events
```

Provider adapter phải map event về `stt`, `tts` và binary Opus cũ; firmware không cần biết provider là cascade hay end-to-end.

## 3. Input admission và conversation gate

Sau khi assistant được đánh thức, mọi audio không được đưa thẳng vào LLM. Voice server chạy một pipeline tổng quát để quyết định input có hợp lệ, có chủ đích, hướng tới robot và đủ tin cậy để tạo một AI turn hay không. Tiếng môi trường, media, giọng người khác, self-playback, đoạn nói vô tình hoặc audio lỗi chỉ là các ví dụ; không được hard-code classifier theo tên từng nguồn âm thanh.

```text
Audio frames
  -> AFE/noise suppression/AGC
  -> VAD + endpoint detection
  -> input quality gate (signal/speech/integrity)
  -> streaming ASR interim/final
  -> admission decision (quality/relevance/confidence)
  -> dialogue act + semantic planner
       ├─ non-actionable input     -> drop, tiếp tục nghe
       ├─ unclear/low confidence    -> hỏi lại tối đa một lần
       ├─ conversation.end          -> goodbye -> standby
       ├─ interrupt                  -> abort ngay, không gọi LLM
       ├─ question/request           -> LLM trả lời
       └─ tool request               -> MCP policy -> tool -> LLM -> TTS
```

### 3.1 Input admission gate

Gate này là model/signal-processing có cấu hình, không phải một loạt ngưỡng rải trong code:

- noise floor calibration khi assistant mở;
- high-pass/noise suppression/AGC phù hợp với INMP441;
- VAD speech probability;
- SNR/energy tối thiểu theo profile phòng;
- độ dài tối thiểu và tối đa của utterance;
- clipping/packet-loss/audio-overrun detection;
- optional target-speaker probability hoặc voiceprint opt-in.

Nếu input không đủ điều kiện tạo turn, server trả `admission.decision=rejected` hoặc `unclear` với reason tổng quát như `non_speech`, `low_quality`, `not_addressed`, `self_echo`, `duplicate` hoặc `low_confidence`; không gọi LLM/MCP và tiếp tục `LISTENING`. Log chỉ giữ metric đã redact, không mặc định lưu raw audio.

VAD chỉ chứng minh có tín hiệu giống lời nói, không chứng minh đó là yêu cầu dành cho robot. `UtteranceGate` phải kết hợp session context, target-speaker probability (nếu user bật), ASR confidence, wake context, semantic relevance và duplicate/self-audio detection. Không được coi “ASR có text” là “người dùng đang hỏi robot”.

### 3.2 Utterance gate và semantic planner

Không dùng một enum duy nhất cho cả chất lượng audio, vai trò hội thoại và hành động. Pipeline trả ba lớp có schema:

```json
{
  "admission": {
    "decision": "accepted",
    "reason": "speech_relevant",
    "confidence": 0.91,
    "addressedToRobot": 0.88
  },
  "dialogueAct": "question",
  "plan": {
    "intent": "weather.current",
    "locale": "vi-VN",
    "action": "call_tool_then_respond",
    "responseRequired": true
  }
}
```

`admission.decision` tối thiểu là `accepted`, `rejected`, `unclear`, `interrupt`, `end`. Reason code tổng quát có thể là `non_speech`, `low_quality`, `not_addressed`, `self_echo`, `duplicate` hoặc `low_confidence`; nguồn âm cụ thể chỉ xuất hiện trong benchmark/telemetry, không tạo branch sản phẩm.

`dialogueAct` tối thiểu gồm `question`, `command`, `follow_up`, `answer`, `confirmation`, `denial`, `correction`, `clarification_answer`, `social`, `interrupt`, `end`. Nhờ vậy các lượt “đúng rồi”, “không phải”, “ý tôi là ngày mai” hoặc xác nhận MCP không bị bỏ chỉ vì không có dạng câu hỏi/command.

`plan.action` tối thiểu gồm `respond`, `call_tool_then_respond`, `ask_clarification`, `execute_pending_tool`, `cancel_pending_tool`, `end_session`, `noop`. Nếu confidence thấp, planner không được tự ý gọi MCP hoặc trả lời chắc chắn. Policy chọn bỏ qua, hỏi lại tối đa một lần hoặc thông báo không nghe rõ theo agent config.

### 3.3 MCP trong conversation gate

MCP chỉ được gọi sau khi utterance gate xác định đây là request/command có chủ đích:

```text
speech -> quality gate -> ASR final -> semantic planner
      -> tool plan -> permission/schema/range validation
      -> MCP call (deadline + cancellation)
      -> result normalized -> LLM response -> streaming TTS
```

Tool result không được đọc thẳng ra loa nếu chứa JSON/kỹ thuật. LLM chuyển result thành câu trả lời tự nhiên theo locale. Nếu tool cần xác nhận, robot hỏi lại và giữ `WAITING_CONFIRMATION`; timeout hoặc abort phải hủy tool call.

## 4. Turn arbiter

Mỗi session có một `TurnArbiter` và `turn_id` tăng dần.

```text
IDLE
  -> LISTENING(turn=12)
  -> THINKING(turn=12)
  -> SPEAKING(turn=12)
  -> LISTENING(turn=13)  # auto mode: VAD tự mở lượt mới

Any state --abort--> CANCELLING(turn=12)
CANCELLING --all tasks cancelled--> LISTENING/IDLE
```

Quy tắc:

- Chỉ turn hiện tại được phép gửi TTS audio.
- Mọi provider task nhận `CancellationToken` và `deadline`.
- Khi `abort`, tăng generation counter; frame/result cũ bị drop dù provider không cancel kịp.
- Tool call phải có timeout riêng và không được giữ audio sender.
- `tts.stop` chỉ phát sau khi audio queue drain hoặc khi explicit abort.

`TurnArbiter` chỉ quản lý lifecycle/cancellation. Nó không quyết định một âm thanh có phải câu hỏi hay không; việc đó thuộc `ConversationGate`/semantic planner.

## 5. Hai cách đánh thức và interrupt

### 5.1 Button wake

```text
standby/idle + short_click -> connecting -> listen:start(mode=auto)
listening    + speech      -> VAD final -> ASR -> LLM -> TTS
thinking     + short_click -> abort -> listening
speaking     + short_click -> abort + clear playback -> listening
listening    + long_press  -> listen:stop -> standby
```

Button wake chỉ mở assistant gate. Trong `mode=auto`, server tự finalize lượt nói theo VAD/silence timeout. Firmware không chờ một lần bấm thứ hai để gửi câu trả lời. Sau `tts.stop`, server gửi lại trạng thái listening nếu gate vẫn mở.

### 5.2 Wake-word wake

Wake detector local là đường đánh thức thứ hai:

```text
standby/sleep + activation wake word -> assistant gate mở -> listen:start(mode=auto)
```

Khi assistant đang `thinking/speaking`, detector có thể nhận `interrupt profile` (ví dụ “dừng lại”, “thôi”, “không nói nữa”). Đây không phải exact-string branch trong firmware: phrase/model/profile được cấu hình theo locale, còn server semantic planner xác nhận ngữ cảnh khi latency cho phép. Với confidence đủ cao, firmware/server phải phát `abort` ngay để không đợi LLM.

Có hai profile khác nhau:

- `activation_profile`: đánh thức từ standby/sleep, ví dụ “Veetee ơi”.
- `interrupt_profile`: dừng AI khi đang thinking/speaking, ví dụ “dừng lại”.

Một profile không được dùng thay cho profile kia vì false positive và UX khác nhau.

### Manual compatibility / accessibility mode

```text
button_down -> listen:start(mode=manual)
button_up   -> listen:stop
```

Chế độ manual vẫn giữ để tương thích Xiaozhi và hỗ trợ người dùng cần kiểm soát capture. Cả hai mode phải dùng cùng command path, không fork logic audio. Agent có thể chọn mode bằng config; không hard-code mode vào provider.

### 5.3 Wake detector lifecycle

Wake detector chạy local trong `standby/sleep` và có thể giữ một interrupt detector nhẹ khi `thinking/speaking`:

1. buffer optional wake audio theo feature flag;
2. gửi `listen:detect` nếu server cần biết phrase;
3. mở assistant gate nếu đang standby;
4. gửi `listen:start(mode=auto)`;
5. bật VAD sau 300-500 ms để không bắt phần đuôi wake phrase.

Khi đang `thinking/speaking`, `interrupt_profile` phải gọi abort trước; clear playback queue, hủy provider/MCP tasks rồi chuyển sang listening. Không biến câu “dừng lại” thành một câu hỏi mới.

## 6. Conversation timeout và standby

Assistant không được giữ session vô hạn. Mỗi session có các deadline cấu hình theo agent/locale, với safe upper bound ở server:

| Timer | Mặc định gợi ý | Behavior |
|---|---:|---|
| `first_input_timeout` | 15 s | sau khi wake nhưng không có speech: goodbye ngắn -> standby |
| `between_turns_timeout` | 30 s | sau khi AI trả lời mà không có lượt mới: chào kết thúc -> standby |
| `closing_grace` | 5 s | nếu user nói/wake trong lúc chào: hủy closing, quay lại listening |
| `max_utterance_duration` | 20 s | finalize hoặc báo câu quá dài, không giữ capture vô hạn |
| `max_session_duration` | 10 phút | absolute ceiling; policy có thể kết thúc sớm hơn |
| `admission_deadline` | 1 s | gate không giữ turn vô hạn |
| `asr_deadline` | 8 s | tính từ VAD final đến ASR final |
| `planner_deadline` | 3 s | structured plan/intent |
| `llm_first_token_deadline` | 5 s | fallback nếu model không stream |
| `tts_first_audio_deadline` | 5 s | fallback hoặc text-only error |
| `mcp_deadline` | 10 s mặc định | override theo tool trong safe range |
| `total_turn_deadline` | 30 s | hard ceiling cho một turn bình thường |

Timeout không gọi LLM chỉ để tạo câu tạm biệt. Server lấy localized template/voice policy từ agent config, ví dụ `session.timeout_goodbye`, phát TTS ngắn, gửi `system.assistant_sleep` và đóng gate. Nếu user bấm nút hoặc nói activation/interrupt profile trước khi kết thúc, `closing` bị cancel và session mới/turn mới được ưu tiên.

Timeout các provider và timeout không có user activity là hai loại khác nhau; không được dùng một timer duy nhất cho cả hai.

Chỉ user activity hợp lệ mới reset inactivity timer. Raw energy, VAD false positive, input bị admission reject hoặc self-echo không được giữ session sống mãi. Một clarification hợp lệ được reset timer tối đa theo `maxClarificationAttempts`; sau đó hệ thống quay lại listening hoặc kết thúc lịch sự theo policy.

## 7. Barge-in và AEC

### Mức triển khai

| Level | Cách | Kết quả |
|---|---|---|
| L0 | Button interrupt | luôn phải pass |
| L1 | Local interrupt profile khi đang nói | best-effort; chỉ thành guarantee sau benchmark |
| L2 | Server AEC timestamp protocol v2 | thử nghiệm, phụ thuộc latency/jitter |
| L3 | Hardware/ESP AFE AEC với far-end reference | mục tiêu full-duplex production |

INMP441 + MAX98357A tách RX/TX nhưng không tự loại echo. Vì vậy `realtime` mode chỉ được bật khi:

- có reference playback được feed vào AEC;
- đo ERLE/false VAD trên board thật;
- có test với tiếng Việt, nhạc nền và khoảng cách loa-mic;
- abort voice <250 ms trong p95.

Nếu chưa đạt, UI phải hiển thị “Bấm nút để ngắt” chứ không hứa “nói chen ngang luôn được”.

## 8. Latency budget

Mục tiêu cascade trên Wi-Fi tốt:

| Đoạn | Mục tiêu p50 | Mục tiêu p95 |
|---|---:|---:|
| Assistant gate/wake -> first uploaded frame | 80 ms | 150 ms |
| VAD final -> ASR final | 250 ms | 600 ms |
| ASR final -> first LLM token | 300 ms | 800 ms |
| First token -> first TTS audio | 250 ms | 700 ms |
| TTS audio -> device speaker | 100 ms | 250 ms |
| **User stop -> speaker silence** | **100 ms** | **250 ms** |

Đo từng đoạn bằng monotonic timestamp và propagate `trace_id`, `session_id`, `turn_id`.

## 9. Vietnamese-first behavior

- ASR giữ dấu và số; không lower-case mù quáng trước intent.
- Chuẩn hóa cách đọc số, ngày, tiền tệ trước TTS.
- Prompt/exit-intent examples/wake profile lưu theo `vi-VN`, fallback `en-US`; exit detection dùng semantic intent thay vì exact string.
- Sentence chunker không cắt sau viết tắt tiếng Việt (`TP.`, `PGS.`, `v.v.`).
- TTS ưu tiên voice nữ/nam `vi-VN` đã benchmark; nếu fail provider, fallback voice khác cùng locale trước khi fallback English.

## 10. Acceptance scenarios

1. Click mở assistant -> nói câu hỏi -> VAD tự finalize -> qua quality/intent gate -> LLM/TTS trả lời, không click lần hai.
2. “Veetee ơi” khi standby -> channel mở và bắt đầu nghe mode auto.
3. Bất kỳ input không hợp lệ/không chủ đích/không hướng tới robot -> bị admission gate loại, không gọi LLM/MCP; tiếng môi trường hoặc media chỉ là test examples.
4. Câu ASR confidence thấp -> robot hỏi lại tối đa một lần, không tự bịa câu trả lời.
5. Câu hỏi cần thiết bị -> planner tạo MCP call, policy validate, tool timeout/cancel đúng và LLM nói kết quả tự nhiên.
6. Click trong lúc thinking/TTS -> im lặng <250 ms p95 -> robot quay lại listening cho turn mới.
7. “Dừng lại”/interrupt profile khi TTS -> nếu local detector đủ confidence thì abort ngay; trước AEC gate đây là best-effort, còn button vẫn là hard guarantee.
8. Không có user speech trong `between_turns_timeout` -> goodbye theo locale -> assistant sleep/standby.
9. User wake/button trong lúc goodbye -> cancel closing -> listening lại.
10. “Tạm biệt” hoặc cách diễn đạt tương đương -> semantic intent `conversation.end` -> không chạy tool/response dư và đóng channel sạch.
11. Wi-Fi drop trong speaking -> device về standby/idle, không kẹt queue.
12. Provider TTS/MCP timeout -> server hủy task, báo lỗi ngắn và cho phép turn mới.
