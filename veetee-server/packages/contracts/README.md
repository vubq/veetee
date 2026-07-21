# contracts

Nguồn chuẩn cho WebSocket/OTA/MCP JSON schemas, conversation policy, binary framing fixtures, OpenAPI types và compatibility tests. Không định nghĩa duplicate event DTO trong từng app.

Device-facing JSON dùng `snake_case`; manager REST có thể dùng `camelCase` qua generated DTO mapping. Fixtures hiện là contract examples; Phase 0 phải bổ sung JSON Schema, generated Python/TypeScript types, C++ parser vectors và signed crypto test vector trước implementation rộng.

`fixtures/config/agent-conversation-policy-v1.json` là baseline cho dual wake, input admission, auto conversation, MCP policy và inactivity timeout. Ngưỡng/model/provider cụ thể vẫn nằm trong versioned agent config, không hard-code vào firmware.

`fixtures/config/provider-baseline-v1.json` là shape canonical `snake_case` cho
Silero/Zipformer/ChunkFormer/9router/VieNeu baseline. Secret và model URL thật
không nằm trong fixture; `base_url_env` chỉ là tên biến môi trường cho dev LAN.

`fixtures/artifacts/resource-manifest-v1.json` và `fixtures/artifacts/device-capability-v1.json` mô tả signed resource bundle, runtime ABI, flash/PSRAM budget và desired/reported compatibility.

`fixtures/devices/reported-state-v1.json` khóa body firmware gửi sau từng phase
resource apply, gồm monotonic version, boot ID, firmware và bounded resource state.

Artifact fixture dùng một chữ ký development có thể verify để cùng một document
chạy qua Node contract test và firmware host test. Vector riêng tại
`fixtures/artifacts/signed-resource-manifest-vector-v1.json` khóa RFC 8785 JCS,
Ed25519 và canonical payload độc lập. Repository chỉ chứa public test key, không
chứa private key hoặc production key. Resource ABI V1 là single-member raw
`srmodels.bin`; manifest ngoài payload là index ký số.
