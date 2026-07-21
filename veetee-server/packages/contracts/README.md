# contracts

Nguồn chuẩn cho WebSocket/OTA/MCP JSON schemas, conversation policy, binary framing fixtures, OpenAPI types và compatibility tests. Không định nghĩa duplicate event DTO trong từng app.

Device-facing JSON dùng `snake_case`; manager REST có thể dùng `camelCase` qua generated DTO mapping. Fixtures hiện là contract examples; Phase 0 phải bổ sung JSON Schema, generated Python/TypeScript types, C++ parser vectors và signed crypto test vector trước implementation rộng.

`fixtures/config/agent-conversation-policy-v1.json` là baseline cho dual wake, input admission, auto conversation, MCP policy và inactivity timeout. Ngưỡng/model/provider cụ thể vẫn nằm trong versioned agent config, không hard-code vào firmware.

`fixtures/config/provider-baseline-v1.json` là shape canonical `snake_case` cho
Silero/Zipformer/ChunkFormer/9router/VieNeu baseline. Secret và model URL thật
không nằm trong fixture; `base_url_env` chỉ là tên biến môi trường cho dev LAN.

`fixtures/artifacts/resource-manifest-v1.json` và `fixtures/artifacts/device-capability-v1.json` mô tả signed resource bundle, runtime ABI, flash/PSRAM budget và desired/reported compatibility.

Artifact shape fixture vẫn dùng placeholder signature. Vector riêng tại
`fixtures/artifacts/signed-resource-manifest-vector-v1.json` khóa RFC 8785 JCS,
Ed25519, canonical payload, public test key và chữ ký có thể verify; không chứa
private key hoặc production key.
