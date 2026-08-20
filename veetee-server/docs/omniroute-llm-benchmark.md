# Benchmark Groq LLM qua OmniRoute

## Phạm vi

Tài liệu này ghi spike M0.3 ngày 20/08/2026 trên OmniRoute local tại
`http://127.0.0.1:20128/v1`. Không lưu API key, prompt người dùng thật hoặc dữ liệu nhạy
cảm. Hai model được gọi qua OpenAI-compatible chat completions với `stream=true`:

- `groq/openai/gpt-oss-120b`, `reasoning_effort=low`.
- `groq/qwen/qwen3.6-27b`, `reasoning_effort=none`.

Script có thể chạy lại tại `veetee-server/tools/benchmark_omniroute.py`; key chỉ đọc từ
`OMNIROUTE_API_KEY`.

## Model lifecycle

Theo catalog Groq truy cập ngày benchmark:

| Model | Stability | Context | Max completion | Developer rate limit |
| --- | --- | ---: | ---: | --- |
| `openai/gpt-oss-120b` | Production | 131.072 | 65.536 | 250K TPM, 1K RPM |
| `qwen/qwen3.6-27b` | Preview | 131.072 | 16.384 | 250K TPM, 1K RPM |

Groq ghi rõ preview chỉ dành cho evaluation và có thể bị dừng với thông báo ngắn.
`llama-3.3-70b-versatile` đã shutdown cho free/developer tier ngày 16/08/2026; Groq đề
nghị hai model trên làm replacement. Catalog OmniRoute vẫn quảng bá ID Llama cũ nhưng
không chứng minh request entitlement, vì vậy Veetee không chọn ID đó làm default.

Nguồn kiểm tra:

- `https://console.groq.com/docs/models`.
- `https://console.groq.com/docs/deprecations`.
- `https://console.groq.com/docs/reasoning`.
- `https://console.groq.com/docs/tool-use`.

## Evaluation set

Mỗi model chạy ba lượt uncached cho bốn case vô hại:

1. Giải thích cầu vồng bằng đúng hai câu tiếng Việt.
2. Trả đúng ba bước tiết kiệm điện, mỗi bước tối đa tám từ.
3. Nhớ tên `Mây` qua một lượt hội thoại và chỉ trả lại tên.
4. Gọi tool `get_weather` với argument thành phố Đà Nẵng.

Mỗi request có nonce trong system message để tránh cache. Một lượt thử trước khi thêm
nonce cho thấy OmniRoute trả lại output/usage cũ trong khoảng 10-20 ms dù reasoning option
đã đổi; benchmark latency và cache correctness phải luôn dùng request độc nhất.

## Kết quả

| Metric | GPT-OSS 120B | Qwen 3.6 27B |
| --- | ---: | ---: |
| HTTP success | 12/12 | 12/12 |
| Prompt/instruction/context đạt | 9/9 | 9/9 |
| Tool call schema đúng | 3/3 | 3/3 |
| Median first output | 601,8 ms | 351,4 ms |
| Min first output | 495,3 ms | 263,5 ms |
| Max first output | 690,5 ms | 2.728,3 ms |
| Client close sau output đầu | 510,5 ms | 273,9 ms |

Qwen nhanh hơn ở median nhưng có một outlier 2,73 giây. GPT-OSS chậm hơn nhưng dải đo ổn
định hơn trong mẫu nhỏ này. Cả hai stream usage metadata và structured tool call; tool
call hiện tới trong một delta ở các lượt đo, nhưng adapter vẫn phải merge delta theo
`index` vì API không đảm bảo luôn gói một event.

Transcript mẫu đã kiểm tra:

```text
User: Chú mèo của tôi tên gì? Chỉ trả lời tên.
GPT-OSS: Mây
Qwen: Mây
```

```text
User: Cho tôi biết thời tiết ở Đà Nẵng hiện tại.
Tool call: get_weather({"city":"Đà Nẵng"})
```

## Reasoning và output handling

- Qwen mặc định có thể stream `<think>` trong content; phải gửi
  `reasoning_effort=none` cho đường hội thoại giọng nói.
- GPT-OSS phải gửi `reasoning_effort=low`. Với output budget 256 ở lượt thử đầu, một case
  dùng gần hết token cho reasoning và không có final content; production phải đặt budget
  đủ và coi empty final content là provider error/retryable policy, không gửi im lặng tới
  TTS.
- Không chuyển reasoning content sang TTS, log hoặc history người dùng.
- Cache key phải bao gồm model, message, tool schema và mọi generation/reasoning option.

## Cancellation và rate limit

Client đóng stream thành công sau output đầu ở cả hai model. Test chỉ chứng minh transport
local được đóng; OpenAI-compatible API hiện không cung cấp bằng chứng rằng compute Groq
upstream đã dừng. Adapter phải propagate cancellation, đóng response và ghi metric
`cancel_requested`/`stream_closed` riêng, không tuyên bố provider compute đã hủy.

Không cố tình tạo burst để ép `429` trên account dùng chung. Rate-limit contract cần test
deterministic bằng fake provider tại M1/M2; integration thật ghi `Retry-After` nếu `429`
xảy ra tự nhiên.

## Khuyến nghị chờ duyệt

- **Default production đề xuất:** `groq/openai/gpt-oss-120b`, vì Groq xếp production và
  latency mẫu vẫn dưới khoảng 0,7 giây tới output đầu.
- **Low-latency candidate/fallback:** `groq/qwen/qwen3.6-27b`, vì median nhanh hơn nhưng
  đang ở preview và có outlier lớn hơn.
- Registry phải allowlist cả hai, model ID/config typed và đổi được không sửa session
  orchestration.

Đây là khuyến nghị M0.3, chưa phải quyết định cuối. Người dùng phải duyệt model mặc định
tại Cổng 0.
