# Benchmark Gemini native TTS và key pool

## Phạm vi

M0.4 được kiểm tra ngày 20/08/2026 bằng native Google Generative Language API, không
đi qua OmniRoute. Credential chỉ đọc từ `veetee-server/.secrets/gemini.env`; báo cáo
không lưu giá trị key.

Model mapping:

| Vai trò | Native model | API mode |
| --- | --- | --- |
| Chính | `gemini-3.1-flash-tts-preview` | `streamGenerateContent?alt=sse` |
| Fallback | `gemini-2.5-flash-preview-tts` | `generateContent` buffered |

Voice probe dùng `Kore`, text tiếng Việt vô hại và response modality `AUDIO`.

## Kết quả native API

| Key slot | Model | Status | SSE events | Audio bytes | First audio | Total |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 3.1 Flash TTS | 200 | 113 | 215.040 | 1.117 ms | 2.378 ms |
| 2 | 3.1 Flash TTS | 200 | 103 | 195.840 | 1.097 ms | 2.159 ms |
| 3 | 3.1 Flash TTS | 200 | 104 | 197.760 | 1.089 ms | 2.197 ms |
| 4 | 3.1 Flash TTS | 200 | 111 | 211.200 | 1.127 ms | 2.319 ms |
| 1 | 2.5 Flash TTS | 200 | 1 | 184.846 | 4.919 ms | 4.919 ms |

Kết luận probe:

- 4/4 key hợp lệ và đều tạo được audio streaming.
- Model 3.1 phát event/audio sớm hơn rõ rệt so với fallback buffered.
- Fallback 2.5 hoạt động nhưng chưa chứng minh streaming vì request buffered.
- Số byte khác nhau giữa key không được coi là chất lượng âm thanh.
- Probe không chứng minh các key thuộc quota độc lập hoặc dùng đồng thời vô hạn.

Artifact raw report nằm ngoài repository tại `/tmp/opencode/veetee-m04-gemini.json`, chỉ
chứa key slot và metric, không chứa credential.

## Key-pool contract đề xuất

Key pool không expose secret cho request handler. Mỗi entry có state nội bộ:

```text
key_id, secret_ref, in_flight, last_used, cooldown_until,
consecutive_failures, circuit_state
```

Policy:

- Chọn entry healthy có `in_flight` thấp nhất; tie-break bằng round-robin.
- Tăng `in_flight` trước request và giảm trong `finally`, kể cả cancellation/exception.
- `401/403`: disable entry và báo credential invalid; không retry cùng key.
- `429`: cooldown theo `Retry-After`, nếu không có thì exponential backoff có jitter.
- `5xx`, disconnect trước audio đầu: retry/failover entry khác trong deadline lượt.
- Lỗi sau audio đầu: không phát lại segment đã gửi.
- Circuit breaker mở sau ngưỡng lỗi liên tiếp và probe half-open sau cooldown.
- Rotation không ảnh hưởng stream đang chạy.

## Test bắt buộc

M1/M2 phải mô phỏng deterministic `401`, `403`, `429`, `5xx`, disconnect trước/sau audio,
cancellation và trạng thái tất cả key unhealthy. Test phải xác nhận giảm `in_flight`,
không retry vô hạn, không replay audio và error typed `provider_unavailable`.

## Giới hạn và quyết định còn mở

- Chưa commit key-pool production implementation; M0 chỉ khóa behavior cần test.
- Chưa kết luận sample rate/codec từ byte count; cần parse `mimeType` và thêm golden audio.
- Chưa thực hiện subjective listening test trên loa thiết bị.
- Cần người dùng duyệt model chính/fallback, voice `Kore`, secret source và quota policy
  tại Cổng 0.
