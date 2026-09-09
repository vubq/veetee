# VeeTee Server Architecture

Cập nhật: 2026-09-09

## Mục đích

Tài liệu này mô tả behavior hiện hành của server VeeTee. Runtime tiếp tục tương thích firmware ESP32/Xiaozhi nguyên bản; semantic decisions do AI thực hiện, còn server giữ protocol, validation và execution safety.

## Pipeline

```text
ESP32/Xiaozhi
  -> audio/protocol events
  -> VAD + ASR
  -> AI turn (context + persona + tool schema)
  -> tool/memory execution nếu được chọn
  -> TTS stream
  -> ESP32 playback
```

## AI boundary

AI quyết định intent, ngôn ngữ, cách diễn đạt, tool/function selection, confirmation, memory mutation và kết thúc hội thoại dựa trên context và schema. Server không dùng keyword matcher, exact phrase matcher, regex classifier hoặc whitelist để route ý định người dùng.

Server chịu trách nhiệm:

- protocol lifecycle và cancellation
- schema validation
- permission/ownership/deadline
- receipt và state invariants

## Context, persona và memory

Persona có thể đến từ cấu hình hoặc saved persona runtime. Context builder phải giữ budget và không làm mất thông tin quan trọng khi persona lớn. Memory được đưa vào AI dưới dạng dữ liệu có cấu trúc; server không tự suy diễn câu người dùng để ghi nhớ.

## Tool flow

Tool result phải có execution outcome rõ ràng. `awaiting confirmation`, `approved`, `succeeded`, `failed` và `unknown` là các trạng thái khác nhau. AI tổng hợp câu trả lời từ receipt thật sau execution.

## Latency path

Các điểm đo cần tách riêng ASR, context, LLM, tool, TTS admission, first PCM/binary và playback vật lý. Sự kiện server gửi audio không chứng minh loa ESP32 đã phát xong hoặc đã dừng.

## Trạng thái

- IMPLEMENTED: các behavior đã có evidence trong status/tests.
- KNOWN_GAP: behavior còn thiếu evidence hoặc còn giới hạn.
- PLANNED: nội dung trong task-plans chưa triển khai.

Xem [VOICE_PIPELINE_STATUS.md](VOICE_PIPELINE_STATUS.md) và [task-plans](../../task-plans/README.md) để xem evidence và công việc tiếp theo.
