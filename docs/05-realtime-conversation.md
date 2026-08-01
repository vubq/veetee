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
  -> openai-compatible-cliproxyapi (streaming LLM local primary)
  -> VieNeu-TTS v3 Turbo (local `vi-VN`)
```

Zipformer và ChunkFormer không chạy đồng thời trên mọi lượt. ChunkFormer cùng
`turn_id` chỉ chạy sau final nếu evaluator thấy confidence/ổn định chưa đạt hoặc
policy yêu cầu chất lượng cao; nếu re-decode không kịp deadline, hệ thống hỏi lại
thay vì gọi LLM với transcript không đáng tin. VieNeu capability phải khai báo
`streaming` hay `batch`; nếu batch, sentence chunker vẫn phát incremental nhưng
không được tính như true streaming.

CLIProxyAPI là adapter OpenAI-compatible local/dev hiện hành, không phải ràng buộc
conversation core. Nó phải pass structured output, tool calling, SSE và cancellation;
chain mặc định hiện chỉ có CLIProxyAPI. Groq vẫn là binding có thể chọn hoặc thêm làm
fallback bằng một publish tường minh trong task sau, không được seed tự động. 9Router
vẫn là adapter tùy chọn nhưng đang tạm dừng. Mọi gateway phải pass conformance;
ChatGPT Plus/Codex login/token không được đưa trực tiếp vào firmware hay provider
secret. Xem `docs/14-model-and-provider-baseline.md` để biết điều kiện freeze.

### 2.1 Streaming liên tục giữa LLM và TTS

Sau khi admission/planner chấp nhận lượt, prose LLM và TTS chạy theo mô hình
producer/consumer có queue giới hạn:

```text
LLM delta -> text delta -> sentence/clause chunker -> bounded speech queue
                                                        └-> TTS stream -> audio
```

Producer tiếp tục đọc delta trong khi consumer đang tổng hợp/phát chunk trước đó.
Chunker ưu tiên dấu kết câu và dấu ngắt mệnh đề, sau đó dùng điểm ngắt theo khoảng
trắng khi stream dài nhưng thiếu dấu câu. `speech_chunk_target_characters` và
`speech_chunk_max_characters` chỉ là pacing/back-pressure bounds; chúng không giới
hạn độ dài câu người dùng, độ dài câu trả lời, số lượt hay thời lượng phiên.

Provider có thể khai báo capability về chi phí khởi tạo mỗi đoạn văn. Với provider
không giữ được một kết nối text incremental, runtime gom các câu ngắn theo
capability đã công bố trước khi mở request tiếp theo. TTS local có thể giữ chunk
nhỏ hơn để giảm thời gian tới audio đầu tiên, nhưng vẫn phải tạo đủ lead-in buffer
để không phát hụt audio khi inference chậm hơn playback.

Queue có giới hạn để giữ memory và latency ổn định. Nếu TTS lỗi, hết deadline hoặc
turn bị abort, producer đang chờ queue phải được đánh thức và hủy cùng generation;
không được để một task treo giữ phiên. Khi hoàn tất bình thường, `tts.stop` chỉ phát
sau khi sentinel đã được đưa vào queue và toàn bộ chunk hợp lệ của turn đã drain;
trường hợp lỗi/hủy được phép phát stop để dọn playback. Output của generation cũ
vẫn bị loại ngay cả khi provider không dừng kịp.

`llmSeconds` là deadline không hoạt động: áp dụng khi chờ first token và được làm
mới trước mỗi SSE event/delta tiếp theo. Thời gian TTS đang drain queue hoặc tổng
thời lượng câu trả lời không tiêu thụ một absolute LLM deadline. Vì vậy một câu
trả lời dài tiếp tục cho tới khi stream hoàn tất; chỉ provider im lặng quá
`llmSeconds`, parent ceiling explicit, user abort hoặc lỗi transport mới dừng turn.
Nếu turn dừng bất thường sau khi audio đã bắt đầu, `tts.stop` phải mang trạng thái
cancelled cho Lab và server phải bỏ paced-audio queue của device để audio cũ không
chạy đè lên câu trả lời của lượt mới.

`ttsFirstAudioSeconds` chỉ áp dụng tới PCM không rỗng đầu tiên của toàn speech turn.
Các batch VieNeu tiếp theo không được reset budget first-audio; chúng dùng
`ttsStreamIdleSeconds`/`ttsSeconds` để bắt khoảng im lặng giữa hai audio event. Log
`conversation_tts_first_audio` vì vậy chỉ xuất hiện một lần mỗi turn, còn
`conversation_tts_batch_first_audio` đo từng request với batch number, reason và số
ký tự. Client vẫn chỉ thấy một lifecycle `tts.start`/audio/`tts.stop` liên tục.

`ttsSeconds` cũng là deadline không hoạt động, được làm mới trước khi chờ mỗi audio
chunk. Với VieNeu local dùng một inference worker, thời gian một lượt phải chờ lượt
đang nói trước đó không tiêu thụ deadline này. Khi bắt đầu phát, lượt giữ worker cho
tới hết speech queue để các phiên đồng thời không giành model sau từng câu và tạo
khoảng lặng dài. Baseline ONNX gom các câu ngắn hoàn chỉnh tới natural cap 160 ký tự; native batch-only
giữ cap 72. Cả hai phát ở sentence boundary và chỉ emergency-split stream thiếu dấu câu
ở bound ONNX 256/native 72; vì vậy không tách cụm từ tại mốc pacing và không chờ toàn bộ
câu trả lời. Tốc độ
voice config được áp dụng đúng bằng post-processing giữ cao độ, không tự giảm trong
lượt. Adapter đo và log tốc độ realtime tối đa theo RTF/headroom; Manager cảnh báo
khi cấu hình trên 1,2x có thể phát nhanh hơn throughput CPU, nhưng không âm thầm
đổi giá trị người dùng đã publish.

Lab gửi PCM sớm để browser lập lịch liền mạch nhưng chỉ phát `tts.stop` và
`listen.start` sau thời lượng PCM đã lên lịch. Nếu timeline đã cạn trước PCM kế tiếp,
Lab cộng lại restart cushion 200 ms giống browser và ghi schedule-gap estimate; đó
không phải playback ACK hoặc measured speaker underrun. Device sink tương tự giữ Opus
queue 5 giây có giới hạn rồi đợi paced sender drain. Paced sender tách provider
queue-starvation khỏi event-loop/WebSocket scheduler lateness với tolerance bounded;
hai metric vẫn không chứng minh loa đã underrun. Abort/cancel đánh thức cả hai ngay,
xóa audio cũ và không phải chờ playback bình thường.

Voice-server ghi hai mốc metric đã redact cho từng turn:

- `conversation_llm_first_token`: ASR/planner xong đến delta LLM đầu tiên.
- `tts.first_audio`: bắt đầu gửi packet audio thực tế đến sink/device.

Các mốc này dùng để đo p50/p95 và phát hiện provider chậm; không phải timeout mới
hay điều kiện tự đóng phiên. Provider deadline của LLM/TTS vẫn độc lập, còn
`max_utterance_duration`, `max_session_duration` và `total_turn_deadline` mặc định
đều tắt.

Không có product cap 5 hay 10 phút cho truyện, response hoặc nội dung đọc từ file.
Mặc định runtime dùng `max_session_seconds=0`, `total_turn_seconds=0`,
`llm_total_seconds=0` và `tts_total_seconds=0`; LLM/TTS không có total-duration
deadline ngầm. First-token/first-audio và stream-idle deadline chỉ bắt provider bị
treo và được refresh khi token/audio chunk tiếp tục tới. Speech queue và playback
queue vẫn bounded, nên producer chịu backpressure thay vì giữ toàn bộ response trong
RAM. Soak 300--600 giây chỉ là cửa sổ representative thuận tiện cho release; output
trên 600 giây vẫn hợp lệ và không được truncate chỉ vì vượt bài test đó.

`maxCompletionTokens`/`max_tokens` là resource bound của **một request LLM**, không phải
duration cap của sản phẩm. Adapter phải bảo toàn terminal `finish_reason`. Nếu prose
stream kết thúc bằng `length` hoặc `max_tokens`, engine drain phần TTS đã phát, emit lỗi
bounded `llm_output_truncated` và không commit cặp user/assistant partial vào context hay
cross-session memory; tuyệt đối không báo response đã hoàn tất im lặng.

Workflow sinh nội dung dài hơn giới hạn một request phải chia thành segment có
continuation/cursor bền vững, generation/cancel chung và điều kiện hoàn tất explicit;
mỗi segment vẫn có token bound riêng. Workflow đọc file/source text không dùng LLM để
tạo lại toàn bộ nội dung: nó stream source theo chunk bounded vào sentence chunker ->
TTS, giữ offset/checkpoint để resume. Hai workflow này là contract bắt buộc cho output
tùy ý dài; prose turn một request hiện bị coi incomplete khi terminal reason là
`length|max_tokens`; các terminal reason/EOF khác tiếp tục theo conformance contract
của adapter, không được tự suy ra từ độ dài audio.

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

Admission dùng đúng sáu trạng thái runtime: `accepted`, `non_actionable`,
`not_addressed`, `unclear`, `interrupt`, `end`. Không dùng một nhãn `rejected` chung vì
nó làm mất nguyên nhân và khiến UI/telemetry khó phân biệt:

| Decision | Khi nào dùng | Hành vi |
| --- | --- | --- |
| `accepted` | Có lượt ngôn ngữ đáng tin cậy và có chủ đích trong phiên, kể cả câu tiếp lời ngắn hoặc câu còn thiếu chi tiết | Chuyển sang planner; planner có thể trả lời, gọi tool hoặc hỏi làm rõ |
| `non_actionable` | Không có nội dung ngôn ngữ dùng được, audio hỏng/quá kém, self-echo hoặc duplicate | Không gọi LLM/MCP, tiếp tục nghe |
| `not_addressed` | Có lời nói rõ nhưng bằng chứng cho thấy đang nói với người khác hoặc là media/ngữ cảnh ngoài phiên | Không gọi LLM/MCP, tiếp tục nghe |
| `unclear` | Sau khi kết hợp signal, ASR, addressing và context, bằng chứng vẫn xung đột hoặc không đủ để chọn an toàn một trong ba trạng thái trên | Không gọi MCP; tiếp tục nghe và có thể hỏi lại một lần theo policy |
| `interrupt` | Lượt có chủ đích ngắt output/công việc hiện tại | Dùng chung đường cancellation |
| `end` | Người dùng có chủ đích kết thúc phiên | Chào kết thúc rồi về standby |

Reason tổng quát gồm `non_speech`, `low_quality`, `not_addressed`, `self_echo`,
`duplicate`, `low_confidence` hoặc `invalid_model_output`. Log chỉ giữ metric đã redact,
không mặc định lưu raw audio.

VAD chỉ chứng minh có tín hiệu giống lời nói, không chứng minh đó là yêu cầu dành cho robot. `UtteranceGate` phải kết hợp session context, target-speaker probability (nếu user bật), ASR confidence, wake context, semantic relevance và duplicate/self-audio detection. Không được coi “ASR có text” là “người dùng đang hỏi robot”.

Baseline device-mic chạy một quality gate rẻ trước semantic provider. Khi đủ telemetry,
input phải đạt ít nhất hai trong ba bằng chứng độc lập: mức tín hiệu near-field, SNR sạch
so với noise reference, và VAD speech đủ dày. Mặc định tương ứng
`VEETEE_ADMISSION_MIN_SIGNAL_SUPPORTS=2`, RMS `-28 dBFS`, SNR `8 dB`, VAD mean/ratio
`0.55/0.55`. Thiếu một kênh telemetry là `unknown`, không phải fail; typed text không
đi qua signal gate. Cách fusion này cho phép giọng nhỏ nhưng sạch, giọng lớn trong phòng
ồn hoặc câu ngắn rõ vẫn đi tiếp, nhưng một feature speech-like đơn lẻ không đủ tạo lượt
LLM/MCP. Riêng candidate tối đa 3 ký tự và 1.200 ms yêu cầu đủ 3/3 support để một xung
âm ngắn có mức/SNR cao nhưng VAD thưa không biến thành follow-up giả. Threshold nằm
trong runtime config và phải A/B bằng corpus đa dạng, không được
thay bằng rule theo tên nguồn âm hoặc exact transcript.

Sau một assistant turn, noise reference có thể còn bị nhiễm tail playback hoặc speech
onset dù follow-up thật có mức near-field mạnh. Với transcript dài hơn short-candidate,
runtime cho semantic gate quyết định tiếp nếu last context là assistant, RMS đạt ngưỡng,
VAD peak ít nhất `0.9` và cả VAD mean/ratio đạt ít nhất `0.85` lần ngưỡng dense. Đây chỉ
là contextual signal admission, không tự chấp nhận intent/tool; corpus âm nền vẫn phải
fail nếu thiếu một trong các bằng chứng này. Hai factor là runtime config, không phrase rule.

Context của một phiên là một cửa sổ in-memory có giới hạn gồm các lượt `user` và
`assistant` gần nhất; không ghi vào Manager DB. Gate nhận cả cửa sổ này cùng transcript
hiện tại. Khi assistant vừa nói xong, một câu đáp ngắn, phản ứng, câu đùa, phủ định,
đính chính, xác nhận hoặc follow-up vẫn là hoạt động hội thoại hợp lệ nếu còn tín hiệu
ngôn ngữ và có thể liên hệ với context. Không yêu cầu câu đó phải tự đứng độc lập như một
command hoặc question.

Cross-session memory là lớp riêng và mặc định tắt. Với thiết bị đã xác thực, Voice chỉ
load một snapshot bounded đúng một lần khi mở session nếu immutable `memoryPolicy` bật
đồng thời `consent`; không truy vấn Manager theo audio frame hoặc theo turn. Snapshot gồm
lịch sử gần đây và structured facts có source/timestamp/expiry, được đưa vào model trong
khối `untrusted_cross_session_memory` ở data/user context, không bao giờ là system
authority. Runtime chỉ enqueue cặp user/assistant sau khi admission hợp lệ và toàn bộ câu
trả lời/TTS hoàn tất; turn reject, abort, partial output hoặc provider error không được lưu.
Queue ghi có giới hạn, deadline và idempotency key nên lỗi Manager không làm hỏng realtime.
Realtime Lab không đọc/ghi durable memory nếu chưa có một opt-in riêng để tránh dữ liệu
operator test làm bẩn memory của thiết bị thật.

Consent có kill-switch ở current published policy: publish `consent=false`,
`enabled=false` hoặc tắt từng store flag chặn ngay mọi Manager load/write mới, kể cả
request còn mang `configVersion` cũ. Session đã load context vào RAM không bị Manager
đẩy dữ liệu ngược vào hot path, nhưng không được append thêm sau khi revoke; session mới
nhận context rỗng. Thay đổi publish không liên quan vẫn giữ memory nếu policy không đổi.

Một lượt chắc chắn hướng tới assistant nhưng còn thiếu tham số, có đại từ chưa rõ hoặc
có nhiều cách hiểu vẫn là `accepted`; planner chọn `ask_clarification`. `unclear` chỉ là
trạng thái không chắc ở ranh giới admission, không phải nhãn cho mọi câu AI chưa hiểu.
Các trường hợp duy nhất được phép đánh dấu `unclear` là:

1. ASR có nhiều giả thuyết cạnh tranh hoặc final không ổn định đến mức cách hiểu thay đổi
   đáng kể, nhưng chưa đủ tệ để kết luận `non_actionable`.
2. Signal bị suy giảm tạo ra một mảnh lời nói có vẻ hướng tới assistant nhưng không đủ
   bằng chứng để xác nhận đó là một lượt ngôn ngữ dùng được.
3. Bằng chứng addressing xung đột: wake/context cho thấy đang trong hội thoại nhưng
   speaker/relevance/self-playback evidence lại cho thấy input có thể là ngẫu nhiên.
4. Semantic gate trả output thiếu field, sai schema hoặc ngoài enum; runtime dùng
   `reason_code=invalid_model_output` thay vì đoán ý hoặc gọi MCP.

Không được đánh dấu `unclear` chỉ vì câu ngắn, là tiếng lóng/câu đùa, thiếu động từ, đổi
ngôn ngữ, không khớp intent đã biết, thiếu tham số tool hoặc yêu cầu không được hỗ trợ.
Các lượt đó được nhận là `accepted`; planner trả lời tự nhiên, hỏi làm rõ hoặc giải thích
giới hạn theo agent config.

Ngược lại, input chỉ được loại ở admission khi không có tín hiệu ngôn ngữ đáng tin cậy,
chất lượng ASR/signal không đủ, là self-echo/duplicate hoặc không có bằng chứng đang
hướng tới assistant. “Quạt”, “TV”, “tiếng xe” và các nguồn môi trường khác chỉ là nhãn
benchmark/telemetry; runtime dùng feature chất lượng và semantic model tổng quát, không
hard-code danh sách nguồn hay cụm từ.

Khi input đến từ `typed_text`, đó là một lượt người dùng chủ động gửi nên không được
đánh dấu `not_addressed` chỉ vì ngắn, đùa hoặc không có intent đã biết. Với audio trong
phiên đã mở bằng button/wake-word hoặc có context hội thoại, cùng nguyên tắc được áp
dụng nếu không có bằng chứng cụ thể về self-echo, duplicate, signal hỏng hay speech
không dùng được. Đây là lớp hòa giải bằng evidence/context sau model, không phải danh
sách phrase.

### 3.1.1 Bằng chứng được truyền vào semantic context

Sau khi VAD finalize, voice-server tổng hợp một object bounded cho chính utterance đó,
không gửi raw PCM hoặc xác suất theo từng frame vào LLM. Object gồm:

- `source` và `wake_source` (`button`/`wake_word`) để model biết phiên được mở bằng
  đường nào;
- số frame VAD, mean/peak/speech-ratio, thời lượng, RMS/peak dBFS, noise reference,
  SNR ước lượng và clipping ratio;
- cờ server buffer bị cắt, cùng các trường `null` cho packet loss/audio overrun mà
  WebSocket V1 không đo được;
- `aec.enabled`, `self_echo_probability` và `target_speaker_probability`, trong đó
  `null` có nghĩa là chưa có bộ đo tương ứng, không phải xác suất bằng 0.

Pinned Zipformer offline hiện chưa expose confidence hoặc partial stability đã hiệu
chuẩn; server giữ cả `confidence=null` và `stability=null` thay vì tự bịa điểm số.
Admission/planner và prose LLM đều nhận
object evidence này cùng lịch sử hội thoại. Prose LLM còn nhận rõ ASR confidence/stability,
admission decision/confidence/addressing/reason, dialogue plan và số lượng context; system
prompt chứa agent snapshot đã publish (tên, locale, timezone thiết bị, personality,
policy version và tool catalog). Các trường này chỉ là context hỗ trợ, không phải lệnh
runtime; VAD chỉ chứng minh audio giống lời nói, không chứng minh người dùng đang nói với
assistant. Các kích thước prompt, schema, catalog và thời gian structured call được
log dạng metric đã redact để tối ưu latency. Prompt/schema được cache theo chính
fingerprint tool catalog của session và tự rebuild sau asynchronous device MCP
discovery; không freeze catalog rỗng lúc WebSocket vừa mở.

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

`admission.decision` là `accepted`, `non_actionable`, `not_addressed`, `unclear`,
`interrupt` hoặc `end`. Reason code tổng quát có thể là `speech_relevant`,
`non_speech`, `low_quality`, `not_addressed`, `self_echo`, `duplicate`,
`low_confidence`, `semantic_interrupt`, `conversation_end`, `unclear` hoặc
`invalid_model_output`; nguồn âm cụ thể chỉ xuất hiện trong benchmark/telemetry, không
tạo branch sản phẩm.

`dialogueAct` tối thiểu gồm `question`, `command`, `follow_up`, `answer`, `confirmation`, `denial`, `correction`, `clarification_answer`, `social`, `interrupt`, `end`. Nhờ vậy các lượt “đúng rồi”, “không phải”, “ý tôi là ngày mai” hoặc xác nhận MCP không bị bỏ chỉ vì không có dạng câu hỏi/command.

`plan.action` tối thiểu gồm `respond`, `call_tool_then_respond`, `ask_clarification`, `execute_pending_tool`, `cancel_pending_tool`, `end_session`, `noop`. Nếu confidence thấp, planner không được tự ý gọi MCP hoặc trả lời chắc chắn. Policy chọn bỏ qua, hỏi lại tối đa một lần hoặc thông báo không nghe rõ theo agent config. Với câu tiếp lời hợp lệ trong context, planner phải được phép chọn `respond` hoặc `ask_clarification` thay vì bị chặn ở admission chỉ vì câu ngắn.

### 3.3 MCP trong conversation gate

MCP chỉ được gọi sau khi utterance gate xác định đây là request/command có chủ đích:

```text
speech -> quality gate -> ASR final -> semantic planner
      -> tool plan -> permission/schema/range validation
      -> MCP call (deadline + cancellation)
      -> result normalized -> LLM response -> streaming TTS
```

Tool result không được đọc thẳng ra loa nếu chứa JSON/kỹ thuật. LLM chuyển result thành câu trả lời tự nhiên theo locale. Nếu tool cần xác nhận, robot hỏi lại và giữ `WAITING_CONFIRMATION`; timeout hoặc abort phải hủy tool call.

Một streaming tool có thể sở hữu lifecycle output bounded riêng trong cùng turn. Trước
khi dispatch, prose LLM phải tạo một thông báo ngắn theo persona/locale rằng stream sắp
bắt đầu và TTS phải phát xong thông báo đó; không hard-code câu thông báo trong runtime.
Sau `tts.stop` của tool, arbiter đưa đúng generation hiện tại từ `SPEAKING` về
`THINKING`, rồi LLM dùng tool result để báo trạng thái thật. Với kết quả đã phát hết,
LLM báo đã hoàn tất và hỏi tự nhiên người dùng có muốn nghe tiếp hay không. Abort/stale
generation không được đi qua các transition này hoặc phát output mới.

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
- Khi WebSocket đóng, sink phải chặn writer mới trước lần `await` cleanup đầu tiên,
  đánh thức mọi producer đang chờ queue, cancel/await paced sender và consume lỗi
  transport. Media/provider cleanup sau đó chỉ được drop output; không được ghi lên
  socket đã đóng hoặc để lại `Queue.put`/`Event.wait` task pending.
- Server prebuffer tối đa ba frame 60 ms; sau lúc client gửi abort có thể còn hai
  frame đã nằm trên wire, nhưng playback generation local phải drop chúng và đưa
  loa về im lặng trong budget 250 ms mà không đóng session.

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

- `activation_profile`: đánh thức từ standby/sleep; phrase sản phẩm V1 là “Hey VeeTee”, cách đọc “hây vi ti”.
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
2. gửi `listen:detect(source=wake_word)` không cần hard-code phrase;
3. nếu signed `send_wake_audio=true`, gửi ring Opus PSRAM bounded 1,92 giây;
4. mở assistant gate bằng `listen:start(mode=auto,source=wake_word)`;
5. bật VAD sau 300-500 ms để không bắt phần đuôi wake phrase.

Voice nhận sequence `detect -> binary -> start` bằng pending buffer tối đa 2 giây gắn
với session generation. `start` khớp phải đưa PCM đó vào VAD theo frame thay vì gọi
reset làm mất cache. Abort/stop/socket close, source hoặc generation không khớp đều xóa
pending data; raw audio không được persist hay đưa vào telemetry.

Khi đang `thinking/speaking`, `interrupt_profile` phải gọi abort trước; clear playback queue, hủy provider/MCP tasks rồi chuyển sang listening. Không biến câu “dừng lại” thành một câu hỏi mới.

## 6. Conversation timeout và standby

Trải nghiệm mặc định không có absolute session ceiling hoặc parent turn ceiling. Mỗi
lượt vẫn kết thúc bằng VAD/endpoint detection (sau đó ASR chốt transcript), không phải
bằng timer tổng phiên. Chỉ khi assistant đang chờ một hoạt động hội thoại hợp lệ mới
chạy inactivity timer:

| Timer | Mặc định gợi ý | Behavior |
|---|---:|---|
| `first_input_timeout` | 180 s | sau khi wake nhưng không có hoạt động hợp lệ: goodbye ngắn -> standby |
| `between_turns_timeout` | 180 s | sau khi AI trả lời mà không có lượt mới: chào kết thúc -> standby |
| `closing_grace` | 5 s | nếu user nói/wake trong lúc chào: hủy closing, quay lại listening |
| `max_utterance_duration` | `0` | disabled; VAD silence/endpoint detection chốt câu |
| `max_session_duration` | `0` | disabled; không tự đóng phiên khi vẫn còn hoạt động |
| `admission_deadline` | 1 s | gate không giữ turn vô hạn |
| `asr_deadline` | 8 s | tính từ VAD final đến ASR final |
| `planner_deadline` | 15 s ceiling | structured plan/intent; mục tiêu LAN <6 s |
| `llm_first_token_deadline` (`llmFirstTokenSeconds`) | 5 s | fallback nếu model không bắt đầu stream |
| `llm_stream_idle_deadline` (`llmSeconds`) | 20 s safety default | làm mới theo mỗi event, không giới hạn tổng câu trả lời |
| `llm_total_deadline` (`llm_total_seconds`) | `0` | disabled; chỉ bật như parent ceiling explicit |
| `tts_first_audio_deadline` (`ttsFirstAudioSeconds`) | 5 s | fallback nếu provider chưa sinh audio đầu tiên |
| `tts_stream_idle_deadline` (`ttsSeconds`) | 10 s safety default | làm mới theo mỗi audio chunk sau khi worker đã được cấp |
| `tts_total_deadline` (`tts_total_seconds`) | `0` | disabled; playback/backpressure không tiêu thụ total cap ngầm |
| `mcp_deadline` | 10 s mặc định | override theo tool trong safe range |
| `total_turn_deadline` | `0` | disabled parent ceiling; provider deadlines bên dưới vẫn độc lập |

Timeout không gọi LLM chỉ để tạo câu tạm biệt. Server lấy localized template/voice policy từ agent config, ví dụ `session.timeout_goodbye`, phát TTS ngắn, gửi `system.assistant_sleep` và đóng gate. Nếu user bấm nút hoặc nói activation/interrupt profile trước khi kết thúc, `closing` bị cancel và session mới/turn mới được ưu tiên.

VAD chốt câu ngay khi phát hiện khoảng im lặng cấu hình; `max_utterance_duration=0`
không tạo một mốc thời gian cưỡng bức cắt câu dài. PCM đang chờ ASR vẫn có byte budget
khẩn cấp để lỗi detector không làm cạn RAM; chạm budget phải emit telemetry và finalize
an toàn. Timeout các provider và timeout không có user activity là hai loại khác nhau;
không được dùng một timer duy nhất cho cả hai. Provider deadline chỉ dùng để hủy một
operation bị treo và trả quyền điều khiển cho lượt mới, không đóng session vì người
dùng nói lâu.

Endpoint V1 dùng base silence `400 ms`. Sau tối thiểu `640 ms` voiced speech, nếu
Silero đã từng vượt speech threshold và trailing probability giữ dưới `0.15`, một
quiet endpoint `320 ms` được phép chốt sớm. Câu ngắn/ngập ngừng vẫn dùng base silence;
policy chỉ dựa signal/duration tổng quát, không so khớp phrase. VAD inference chạy
ngoài asyncio event loop và telemetry ghi một lần tại speech end với
`endpoint_reason` cùng tổng processing time, không phát metric theo từng frame.

Chỉ user activity hợp lệ mới reset inactivity timer. Raw energy, VAD false positive, input bị admission reject hoặc self-echo không được giữ session sống mãi. Một clarification hợp lệ được reset timer tối đa theo `maxClarificationAttempts`; sau đó hệ thống quay lại listening hoặc kết thúc lịch sự theo policy.

Nếu admission đã chấp nhận lượt người dùng nhưng LLM/TTS phía sau lỗi, lượt đó vẫn
là hoạt động hội thoại hợp lệ. Sau recovery, `between_turns_timeout` phải được arm
lại đầy đủ từ lúc runtime thực sự quay về `LISTENING`; không được khôi phục deadline
cũ đã chạy trong thời gian xử lý/phát câu trả lời dài.

VAD session giữ pre-roll PCM có giới hạn để không cắt phụ âm đầu khi detector đổi
từ silence sang speech. Baseline là 320 ms qua `VEETEE_VAD_PRE_ROLL_MS`; đây là
audio boundary policy có safe range, không phải rule semantic theo tiếng ồn cụ thể.
Khi finalize utterance, VAD buffer được reset trước lượt tiếp theo để audio cũ không
rò sang ASR request mới.

Trong V1 chưa có AEC đã qua production gate, firmware ngừng uplink capture khi đã
chuyển khỏi `LISTENING`.
Button vẫn có thể abort ngay cả lúc server đang chạy ASR nhưng firmware chưa nhận
transcript: click ở `LISTENING` được hiểu là cancel pending turn rồi quay lại nghe,
không đóng assistant gate. Long press mới là thao tác tắt gate.

## 7. Barge-in và AEC

### Mức triển khai

| Level | Cách | Kết quả |
|---|---|---|
| L0 | Button interrupt | luôn phải pass |
| L1 | Local interrupt profile khi đang nói | best-effort; chỉ thành guarantee sau benchmark |
| L2 | Server AEC timestamp protocol v2 | thử nghiệm, phụ thuộc latency/jitter |
| L3 | Hardware/ESP AFE AEC với far-end reference | mục tiêu full-duplex production |

Lát firmware hiện có `CONFIG_VEETEE_EXPERIMENTAL_AEC=n` để tạo evidence cho L1/L3
mà không nâng capability sớm. Khi opt-in build bật, direct ESP-SR 2.4.7 AEC nhận mic
16 kHz và far-end từ đúng PCM speaker 24 kHz sau volume, downsample 3:2 qua buffer
bị chặn kích thước. Output chỉ đi tới local interrupt detector trong thời gian loa
phát. `features.aec`, conversation capture khi `SPEAKING` và `listen.mode=realtime`
vẫn giữ false/off; chúng không phụ thuộc đơn thuần vào compile flag.

INMP441 + MAX98357A tách RX/TX nhưng không tự loại echo. Vì vậy `realtime` mode chỉ được bật khi:

- có reference playback được feed vào AEC;
- đo ERLE/false VAD trên board thật;
- có test với tiếng Việt, nhạc nền và khoảng cách loa-mic;
- abort voice <250 ms trong p95.

Evidence board phải ghi thêm ERLE, near-end attenuation, false activation/interrupt,
reference underflow/drop và heap/task-stack. Host resampler test hoặc firmware build
pass không thay thế phép đo mic/loa thật.

Nếu chưa đạt, UI phải hiển thị “Bấm nút để ngắt” chứ không hứa “nói chen ngang luôn được”.

## 8. Latency budget

Structured conversation gate dùng `response_format=json_schema` strict khi gateway
quảng cáo capability và schema tương thích; CLIProxyAPI dùng JSON Object Mode cho schema
MCP động không đạt ràng buộc strict. Voice Server vẫn validate toàn bộ object bằng Draft 2020-12
JSON Schema ở voice-server trước khi cho phép planner/tool. Adapter giữ mã lỗi bounded
(`invalid_sse_json`, `empty_structured_output`, `invalid_structured_json`,
`structured_output_truncated`, `structured_schema_mismatch`) và không ghi raw output. Các field tương thích bị model bỏ
sót (ví dụ `plan.action`) được chuẩn hóa an toàn từ chính structured fields trước lần
validate cuối; không suy diễn từ exact transcript. Nếu structured output sai schema
nhưng provider vẫn phản hồi, server dùng degraded plan `respond_without_tools` và vẫn
validate policy. Nếu chính semantic provider không khả dụng, engine không gọi lại cùng
LLM lần hai cho prose; nó phát recovery đã cấu hình/local signal và giữ session sống.
Cách này tránh nhân đôi deadline ở đúng thời điểm provider đang lỗi.
Field nullable hoặc enum mở rộng từ JSON Object Mode được chuẩn hóa theo contract;
output không thể phục hồi cũng đi qua prose response không tool, không tự biến speech
hợp lệ thành `unclear`. Provider có `streamProseResponse=true` không dùng
`plan.response_text` cho lượt trả lời thường: prose token được tách theo câu và đưa vào
TTS queue trong lúc LLM vẫn tiếp tục sinh phần sau. Prompt của structured gate cũng bắt
buộc `response_text=null` ở đường này để model chỉ làm admission/planning, không sinh
một câu trả lời hoàn chỉnh rồi bị runtime bỏ đi trước lần prose stream thứ hai.

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

## 9. Web Device Simulator

Manager Web có một client Lab dùng chính `SessionProfile`, `TurnArbiter`, admission,
planner, provider chain, MCP broker, TTS và inactivity controller của voice-server.
Đây là công cụ kiểm thử pipeline thật khi chưa ở cạnh ESP32, không phải một
conversation engine riêng.

| Input | Đường chạy | Phần bị bypass |
|---|---|---|
| Text chat | admission -> planner/LLM/MCP -> VieNeu TTS -> browser | VAD và ASR, có event `vad.bypassed`/`asr.bypassed` |
| Audio Replay | PCM16 mono 16 kHz -> Silero -> Zipformer -> admission -> AI/MCP -> TTS | không bypass stage AI nào |
| Live Mic | browser capture/resample -> cùng pipeline Audio Replay | không bypass stage AI nào |

Lab giữ đúng semantics sản phẩm: bắt đầu phiên chỉ mở assistant gate, audio tự
finalize bằng VAD, interrupt tăng generation và hủy ASR/LLM/TTS/MCP, timeout đưa
assistant về sleep và nút wake mở lại cùng session. Ba MCP mode là `simulated`,
`selected_device` và `disabled`; mode thiết bị thật chỉ dùng được khi robot đang có
voice session hoạt động.

Độ trung thực phải được công bố rõ trong UI và `lab.hello`:

- Browser nhận PCM TTS để phát trực tiếp; Lab không đo packetization/pacing Opus V1,
  Wi-Fi ESP32, decoder queue, amplifier hoặc loa vật lý. Mobile browser cần một user
  gesture để tạo/resume `AudioContext`; nút bắt đầu phiên thử tự unlock bằng silent
  buffer và UI giữ nút `Bật âm thanh` nếu context vẫn suspended hoặc playback lỗi.
- Browser AEC/noise suppression/AGC của Live Mic không phải AEC của INMP441/
  MAX98357A. `getUserMedia` trên LAN HTTP thường bị chặn; dùng HTTPS/localhost hoặc
  Audio Replay.
- Latency timeline đo speech end -> ASR final -> admission -> first text -> first
  audio và abort -> server silence. `abort_to_silence` trên loa vẫn cần playback ACK
  và nghiệm thu board.
- Transcript/audio không được ghi vào Manager DB; raw event chỉ sống trong browser
  session và vẫn phải bounded/redacted.

Manager cấp JWT Lab dùng một lần, TTL 90 giây. Token chỉ gửi trong `lab.auth` frame
đầu tiên của `/veetee/lab/v1/`, không đặt trong URL/query/log. Contract versioned
nằm tại `veetee-server/packages/contracts/fixtures/lab/`.

## 10. Vietnamese-first behavior

- ASR giữ dấu và số; không lower-case mù quáng trước intent.
- Chuẩn hóa cách đọc số, ngày, tiền tệ trước TTS.
- Prompt/exit-intent examples/wake profile lưu theo `vi-VN`, fallback `en-US`; exit detection dùng semantic intent thay vì exact string.
- Sentence chunker không cắt sau viết tắt tiếng Việt (`TP.`, `PGS.`, `v.v.`).
- TTS ưu tiên voice nữ/nam `vi-VN` đã benchmark; nếu fail provider, fallback voice khác cùng locale trước khi fallback English.

## 11. Acceptance scenarios

1. Click mở assistant -> nói câu hỏi -> VAD tự finalize -> qua quality/intent gate -> LLM/TTS trả lời, không click lần hai.
2. “Hey VeeTee” (đọc “hây vi ti”) khi standby -> channel mở và bắt đầu nghe mode auto.
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
13. Text Lab ghi rõ VAD/ASR bypass nhưng vẫn chạy admission/LLM/MCP/TTS thật; Audio
    Replay chạy Silero/Zipformer thật và không tuyên bố đã đo Opus/AEC/loa ESP32.
14. Voice socket lỗi retryable khi gate mở -> hủy output cũ, tạo session V1 mới trong
    budget; protocol error/user cancel không reconnect và không kẹt `CONNECTING`.
15. ASR/TTS/goodbye lỗi trong Device hoặc Lab -> error code/stage bounded, đóng
    closing grace đúng hạn và cho phép lượt mới; public payload không lộ exception.
16. Câu trả lời dài hơn `llmSeconds` trong lúc TTS queue back-pressure vẫn phát hết;
    provider ngừng gửi event quá `llmSeconds` phải hủy turn và dọn audio cũ trước
    khi chấp nhận câu mới.
17. Hai phiên dùng chung VieNeu không xen kẽ sentence chunk; chờ TTS worker lâu hơn
    `ttsSeconds` không làm hỏng turn, còn worker đã nhận lượt nhưng ngừng phát audio
    quá `ttsSeconds` phải hủy và dọn playback.
18. Một response kể chuyện representative tạo ít nhất 300 giây PCM (300--600 giây là
    cửa sổ soak chuẩn, không phải giới hạn sản phẩm) vẫn giữ một lifecycle
    `tts.start` -> binary audio -> `tts.stop`, không provider deadline, schedule gap,
    stale output hoặc absolute-turn cutoff; playback drain không tiêu thụ LLM/TTS idle
    deadline khi stream vẫn có progress.
19. Sau long response, ít nhất ba turn hội thoại thường vẫn hoàn tất và mỗi turn quay
    lại `listen.start`; Lab/browser evidence phải tách khỏi nghe Opus/I2S/loa ESP32 thật.
20. Synthetic progressive stream tương đương hơn 10 phút nói phải hoàn tất nhanh trong
    test với speech queue nhỏ, context/memory tail bounded và idle deadline rất ngắn
    nhưng luôn có progress; test này chứng minh không có total cap mà không phải tạo/lưu
    audio hoặc transcript thật.
