# Ghi chú kỹ thuật server

## Mục đích

Thư mục này gồm hai loại tài liệu. Các tài liệu kiến trúc, pipeline, protocol, provider
và security tổng hợp hành vi từ source tham khảo để nghiên cứu, không định nghĩa kiến
trúc sản phẩm. Riêng `server-implementation-plan.md` là kế hoạch, kiến trúc mục tiêu và
thứ tự thực thi chính thức đã được người dùng phê duyệt ở cấp tài liệu.

Source tham khảo là monorepo gồm Python realtime server, Java management API, Vue web,
uni-app mobile và digital-human test client. Veetee không mặc định phải sử dụng tất cả
các thành phần hoặc cùng công nghệ.

## Danh mục

| Tài liệu | Nội dung |
| --- | --- |
| [Tổng quan kiến trúc](architecture.md) | Thành phần, boundary và deployment mode |
| [Realtime AI pipeline](realtime-ai-pipeline.md) | Connection, audio, VAD, ASR, LLM, tool và TTS |
| [Ma trận tương thích firmware-server](firmware-compatibility-matrix.md) | OTA/config, WebSocket, Opus, state và golden vector cần khóa |
| [Chính sách namespace](namespace-policy.md) | Endpoint, package, metadata và identifier policy |
| [Benchmark Groq LLM](omniroute-llm-benchmark.md) | Streaming, latency, reasoning, tool call và model lifecycle |
| [Benchmark Gemini TTS](gemini-tts-benchmark.md) | Native streaming, fallback và key-pool contract |
| [Benchmark ASR/VAD](asr-vad-benchmark.md) | PhoWhisper small/medium và Silero ONNX |
| [Contract, threat model và parity](m0-contract-and-threat-model.md) | Boundary, event/error semantics, rủi ro và dependency backlog M0.6 |
| [Báo cáo kiểm thử Mốc 1](m1-test-report.md) | Backend fake-AI, simulator, contract/E2E evidence và giới hạn |
| [Báo cáo QA M2.6](m2.6-qa-report.md) | Full-duplex, barge-in, race/flow-control và QA local 100% |
| [Báo cáo M3 AI brain](m3-ai-brain-report.md) | Prompt, dialogue, intent, tool/MCP, memory và audit |
| [Báo cáo M4 control plane](m4-control-plane-report.md) | PostgreSQL, agent API, memory API và Console integration |
| [Báo cáo thực hiện M6](m6-parity-report.md) | Decision record, ma trận capability và bằng chứng parity tích lũy |
| [Giao thức và API](protocols-and-apis.md) | Device WebSocket, HTTP/OTA/vision, MCP và manager API |
| [Provider và cấu hình](providers-and-configuration.md) | Plugin factory, selected modules và config precedence |
| [Bảo mật, vận hành và kiểm thử](security-operations-testing.md) | Auth, secret, scale, observability và test gap |
| [Kế hoạch triển khai Veetee Server](server-implementation-plan.md) | Kiến trúc mục tiêu, provider đã chốt, task và cổng duyệt từng mốc |

## Bản đồ source tham khảo

| Thành phần | Vị trí upstream |
| --- | --- |
| Python realtime server | `../references/xiaozhi-esp32-server/main/xiaozhi-server/` |
| Java management API | `../references/xiaozhi-esp32-server/main/manager-api/` |
| Web console | `../references/xiaozhi-esp32-server/main/manager-web/` |
| Mobile console | `../references/xiaozhi-esp32-server/main/manager-mobile/` |
| Browser test client | `../references/xiaozhi-esp32-server/main/digital-human/` |
| Deployment/integration docs | `../references/xiaozhi-esp32-server/docs/` |

## Cách đọc

- Dùng `architecture.md` để xác định subsystem nào cần tham khảo.
- Dùng `realtime-ai-pipeline.md` khi làm audio/session/AI orchestration.
- Dùng `protocols-and-apis.md` cùng tài liệu firmware khi thay đổi contract thiết bị.
- Dùng `providers-and-configuration.md` khi thêm model/provider.
- Dùng `server-implementation-plan.md` làm thứ tự thực thi chính thức; AI phải dừng ở
  cổng duyệt cuối mỗi mốc.
- Kiểm tra source tại commit đang pin trước khi triển khai wire format hoặc endpoint.
