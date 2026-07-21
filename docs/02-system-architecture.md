# Kiến trúc veetee

## 1. Quyết định tổng thể

`veetee` dùng hai source repository nhưng một bộ contract:

```text
veetee-firmware (ESP-IDF/C++)
        │  OTA bootstrap + WebSocket/MQTT + MCP envelope
        ▼
veetee-server
  ├── voice-server (Python, hot path thoại)
  ├── manager-api (TypeScript/NestJS, control plane)
  ├── manager-web (Vue 3/TypeScript, console)
  └── packages/contracts, provider-sdk, ui-tokens
```

Voice path không đi qua manager API:

```text
ESP32 ──WebSocket binary/text──> voice-server ──provider stream──> ASR/LLM/TTS
   ▲                                  │
   └────────────── Opus TTS ──────────┘

manager-web ──REST──> manager-api ──Postgres/Redis/MinIO
                             │
                             ├── config snapshot / invalidation ──> voice-server
                             └── desired state + signed manifest ──> firmware pulls HTTP(S)
```

Đây là boundary quan trọng: thay provider, deploy UI hoặc migrate database không được làm gián đoạn audio session đang mở.

## 2. Công nghệ mặc định

### Firmware

- ESP-IDF 6.0.2, C++17, CMake/Kconfig.
- `esp_lcd` + driver ST7789; LVGL chỉ bật khi cần UI phức tạp.
- I2S STD simplex: RX INMP441, TX MAX98357A.
- Opus encode/decode; NVS cho settings và UUID.
- `esp_websocket_client`/transport tương thích với contract v1.
- mbedTLS HTTPS; OTA A/B partition và rollback.
- Unity/host test cho parser/state machine; hardware smoke test trên board thật.

### Voice server

- Python 3.12, `asyncio`, Starlette/FastAPI trên Uvicorn cho HTTP và WebSocket trong cùng ASGI process; chỉ dùng standalone `websockets` nếu benchmark chứng minh cần.
- Pydantic v2 cho config/contract; `httpx` cho provider; `orjson` cho JSON hot path.
- Redis cho session lease, cancellation signal, config version và rate limit.
- `structlog` + OpenTelemetry + Prometheus metrics.
- Adapter SDK không import trực tiếp vào transport layer.
- Provider secret chỉ đi qua secret resolver/service credential, không nằm trong agent snapshot và không gửi xuống firmware.
- Baseline local `vi-VN`: Silero VAD -> Sherpa-ONNX Zipformer 30M INT8 ->
  ChunkFormer-CTC-Large-Vie re-decode có điều kiện -> LLM qua
  `openai-compatible-9router` -> VieNeu-TTS v3 Turbo.
- Local speech model là process/worker của server, không nhúng vào ESP32. Worker có
  concurrency limit, health, warmup, cancellation và memory budget riêng.
- Chi tiết capability/gate nằm trong `docs/14-model-and-provider-baseline.md`.

### Manager API

- TypeScript, NestJS chạy trên Fastify.
- PostgreSQL 16, Prisma migrations; Redis cho cache/queue/short-lived activation.
- JWT access token ngắn hạn + refresh token rotation; Argon2id; RBAC/tenant guard.
- BullMQ cho OTA build, asset processing, voice clone, knowledge indexing.
- OpenAPI generated từ controller DTO; package schema dùng chung với web.

### Manager web

- Vue 3 + TypeScript + Vite + Vue Router + Pinia.
- TanStack Vue Query cho server state; Zod cho form/response validation.
- Tailwind CSS + Reka UI/Radix primitives; ECharts cho telemetry.
- i18n BCP-47, mặc định `vi-VN`, fallback `en-US`.
- Playwright cho critical flows và Vitest cho component/contract tests.
- Giữ nguyên visual/interaction của prototype đã duyệt tại
  `veetee-server/prototypes/manager-web/index.html`; các tên provider trong fake data
  không phải model baseline production.

### Hạ tầng

- Docker Compose cho dev: Postgres, Redis, MinIO, voice-server, manager-api, manager-web.
- V1 deploy single-node: 9Router, Silero VAD, Zipformer, ChunkFormer và VieNeu-TTS
  chạy cùng máy với backend dưới process/container riêng. Internal traffic dùng
  `127.0.0.1` hoặc Docker private network; không publish model worker port ra LAN.
- Caddy/Nginx terminate TLS; WebSocket timeout và max frame phải cấu hình rõ.
- Kubernetes chỉ sau khi đã đo connection count, CPU audio và provider quota.

## 3. Module boundary

### `voice-server`

```text
apps/voice-server/
├── transport/       # websocket-v1, mqtt-gateway, framing, auth
├── session/         # connection/session lifecycle, state, cancellation
├── conversation/    # admission gate, semantic planner, turn arbiter, timeout, sentence chunker
├── providers/       # registry + adapters ASR/VAD/LLM/TTS/realtime/memory
├── tools/           # MCP device, remote MCP, server plugin
├── config/          # snapshot loader + ETag/version
├── observability/   # metrics, traces, redaction
└── tests/            # fixtures, contract, latency, chaos
```

### `manager-api`

```text
apps/manager-api/
├── auth/             # tenant, user, role, token
├── agents/           # persona, locale, provider bindings, prompt versions
├── devices/          # registration, activation, status, ownership
├── providers/        # catalog, secret refs, health tests, model configs
├── tools/            # MCP catalog, policy, plugin metadata
├── artifacts/        # wake/model/assets bundle, capability, desired/reported state
├── ota/              # executable firmware artifact, rollout, signed manifest
├── conversations/    # metadata/transcript retention policies
├── i18n/             # locale catalog, wake/exit profiles
└── common/           # pagination, audit, errors, idempotency
```

### `manager-web`

UI không phản chiếu database table một cách máy móc. Navigation theo nhiệm vụ vận hành:

- Overview.
- Devices & pairing.
- Agents & conversation policy.
- Provider hub.
- Realtime lab.
- MCP tools.
- OTA & releases.
- Audit/settings.

## 4. Config flow

1. Admin tạo provider credential trong manager API; secret được mã hóa at rest.
2. Admin gán provider/model/locale/tool/wake/artifact policy vào agent.
3. Manager API tạo immutable `agent_config_version`.
4. Voice server poll hoặc nhận Redis invalidation, tải snapshot theo version.
5. Session mới dùng snapshot mới; session đang chạy giữ snapshot cũ tới khi kết thúc turn.
6. Manager tạo device desired state và signed manifest cho config/resource bundle.
7. Device nhận version invalidation nhỏ, tự pull HTTP(S) qua device-edge, verify, stage inactive slot và apply ở standby/session boundary.
8. Device report effective/reported state; UI hiển thị drift/apply error thay vì coi publish là đã áp dụng.
9. Rollback chỉ đổi pointer version hoặc active slot; không mutate object đã publish.

Chi tiết nằm trong `docs/12-dynamic-config-and-artifacts.md`.

### Device edge và local deployment

`device-edge` là route surface, không phải business domain mới. Trong dev có thể là Caddy/Nginx proxy:

```text
8000  voice-server WebSocket
8002  manager-api admin REST/OpenAPI
8003  device-edge: bootstrap/config/artifact/reported-state
8081  manager-web
20128  9Router loopback-only, không expose trực tiếp cho ESP32/LAN
```

Port 8003 proxy tới device-facing routes của manager-api/object store; không tạo một nguồn dữ liệu thứ hai. Nếu không dùng reverse proxy, manager-api phải expose rõ cả admin và device routes bằng một process/port duy nhất và docs/fixtures phải đổi theo.

## 5. Multi-tenant và mở rộng quốc gia

Mọi entity có `tenant_id`, mọi agent có `default_locale`, `fallback_locales`, `voice_profile` và `wake_profile`. Locale dùng BCP-47, không dùng enum chỉ chứa quốc gia. V1 giữ schema tenant-aware nhưng Manager Web có thể bắt đầu bằng một workspace/owner để giảm scope UI và RBAC. JSON dưới đây là Manager API representation; device snapshot được map sang `snake_case` theo protocol contract.

```json
{
  "defaultLocale": "vi-VN",
  "fallbackLocales": ["en-US"],
  "asrLocales": ["vi-VN", "en-US"],
  "ttsVoices": {
    "vi-VN": "azure:vi-VN-HoaiMyNeural",
    "en-US": "openai:alloy"
  },
  "semanticIntentProfiles": {
    "vi-VN": "intent-profile:vi-v3",
    "en-US": "intent-profile:en-v2"
  },
  "wakeProfile": {
    "id": "wake-vi-home-v4",
    "resourceBundleVersion": "1.4.0"
  }
}
```

## 6. Các nguyên tắc không được phá

- Không để API key/provider SDK lọt vào firmware.
- Không để manager API nằm trên frame-by-frame audio path.
- Hội thoại mặc định dùng `mode=auto`: VAD kết thúc lượt nói và AI tự trả lời; button chỉ mở/tắt assistant gate hoặc abort.
- Button wake và activation wake word phải hội tụ vào cùng session flow; interrupt phrase dùng cùng cancellation path với button.
- Không đưa audio/transcript thẳng vào LLM: input admission + relevance/intent gate phải quyết định có tạo AI turn hay không.
- Session có inactivity timeout, closing grace và provider deadlines riêng; không để AI/MCP treo vô hạn.
- Không hard-code intent, phrase, persona, provider route hoặc locale behavior; dùng model output có schema, agent config và registry.
- Chỉ giữ deterministic logic cho hardware, protocol, security, safety, resource bounds, state/cancellation và recovery.
- Config/model/assets dùng immutable desired state + signed bundle; WebSocket chỉ invalidation, firmware tự pull/verify/apply.
- Resource bundle chỉ chứa data/model/assets. Native runtime/operator thay đổi phải đi qua signed executable OTA.
- Không gọi blocking I/O trong firmware main loop/audio task.
- Không coi transcript hoặc tool call là trusted input; validate schema + authorization.
- Không thêm provider mới bằng cách sửa `if/else` trung tâm; phải đăng ký qua registry.
- Không thay đổi wire semantics mà không thêm contract fixture và compatibility test.
