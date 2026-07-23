# veetee

`veetee` là blueprint và điểm khởi đầu để xây dựng một robot AI thoại ưu tiên tiếng Việt trên ESP32-S3 N16R8. Dự án giữ wire contract của Xiaozhi để tận dụng firmware/server ecosystem đã trưởng thành, nhưng tách lại boundary, tên gọi, cấu hình, provider và quy trình phát triển để dễ đọc, kiểm thử và mở rộng đa ngôn ngữ.

## Mục tiêu phiên bản đầu

- Firmware riêng cho ESP32-S3 N16R8, màn ST7789, mic INMP441 và loa MAX98357A.
- `voice-server` xử lý dual wake, input admission, phiên thoại realtime, timeout và provider AI/MCP.
- `manager-api` quản lý tenant, agent, device, provider, MCP, OTA và audit.
- `manager-web` là console vận hành; prototype HTML nằm tại `veetee-server/prototypes/manager-web/`.
- Giữ tương thích WebSocket v1, MQTT + UDP, OTA/bootstrap, activation code 6 số và MCP JSON-RPC 2.0.
- Tiếng Việt là locale mặc định; mọi text, wake profile, prompt, voice và provider đều đi qua BCP-47 locale.

## Bản đồ thư mục

```text
veetee/
├── docs/                         # vision, audit, contracts, roadmap và tiêu chuẩn
├── skills/veetee-development/    # skill để AI triển khai đúng workflow của dự án
├── veetee-firmware/              # source repo thứ nhất (ESP-IDF)
└── veetee-server/                # source repo thứ hai (voice-server + API + web)
```

## Đọc theo thứ tự

1. `docs/01-xiaozhi-audit.md` - những gì source tham chiếu đang làm.
2. `docs/02-system-architecture.md` - boundary mới và công nghệ được chọn.
3. `docs/03-firmware-spec.md` - phần cứng, state machine và Wi-Fi/activation.
4. `docs/04-protocol-compatibility.md` - contract không được phá vỡ.
5. `docs/05-realtime-conversation.md` - auto conversation, assistant gate, wake word, barge-in và latency budget.
6. `docs/06-provider-and-mcp.md` - provider registry, MCP và tool policy.
7. `docs/07-manager-product-spec.md` - API/data model/UI scope.
8. `docs/08-roadmap.md` - milestone, Definition of Done và thứ tự giao việc cho AI.
9. `docs/09-testing-security-operations.md` - test, observability, security và release.
10. `docs/10-ai-development-playbook.md` - cách giao task cho AI theo skill.
11. `docs/11-ai-first-design-principles.md` - ranh giới giữa AI behavior và runtime deterministic bắt buộc.
12. `docs/12-dynamic-config-and-artifacts.md` - cấu hình wake/model/assets qua Web/API, signed bundle, apply và rollback.
13. `docs/13-decision-register.md` - quyết định đã chốt, mặc định đề xuất và checklist phải xác nhận trước khi code.
14. `docs/14-model-and-provider-baseline.md` - baseline local ASR/VAD/TTS, 9router LLM, fallback và benchmark gate.
15. `docs/15-local-ai-runtime.md` - runtime local đã chọn, readiness, model worker và validation.
16. `docs/16-device-ui-and-ui-packs.md` - contract và hướng triển khai UI Pack data-only.

## Tài liệu tham khảo bổ sung

- `docs/17-xiaozhi-useful-capabilities.md` - capability inventory tham khảo từ Xiaozhi, không phải plan task.
- `docs/18-device-diagnostics.md` - contract audio debugger, self-test và device health P0.
- `docs/19-signed-firmware-ota.md` - firmware OTA ký số, canary/% rollout và rollback.
- `docs/20-agent-prompts-and-personalities.md` - prompt template, biến allowlist và personality catalog.

## Source tham chiếu

Hai thư mục trong `references/` chỉ dùng để đọc và đối chiếu, không chỉnh sửa:

- `references/xiaozhi-esp32`
- `references/xiaozhi-esp32-server`

Cả hai source có MIT license. Nếu copy hoặc sửa code trực tiếp, giữ copyright notice tương ứng; phần code mới của `veetee` phải có ownership/license được quyết định trong ADR trước khi phát hành.

## Trạng thái

Repository đã có baseline chạy được cho firmware ESP32-S3, Wi-Fi provisioning,
activation/bootstrap, state UI, WebSocket V1, raw Opus hai chiều, voice-server
cascade local, provider failover và Manager API/Web nối dữ liệu thật. Đây là V1
development baseline; custom `Hey VeeTee`, AEC/full voice barge-in, hardware soak,
production TLS/secret manager và rollout hardening vẫn phải qua release gate.

Pin map hiện tại đã bring-up thành công trên prototype đang cắm với ST7789,
INMP441 và MAX98357A. Pin vẫn được coi là board profile V1 có version; phải cập nhật
ADR nếu schematic hoặc một revision phần cứng khác yêu cầu thay đổi.
