# veetee-server

Backend monorepo gồm realtime voice hot path, manager control plane, web console và
các contract dùng chung. Luồng development mặc định chạy trực tiếp trên máy để
giảm overhead và dễ monitor model/GPU; Docker chỉ là tùy chọn cho hạ tầng stateful.

## Chạy local

```bash
cp .env.example .env
npm ci
uv sync --project apps/voice-server --all-groups
npm run dev:voice
```

PostgreSQL/Redis có thể dùng bản cài trên host hoặc khởi động riêng bằng:

```bash
npm run infra:up
```

Lệnh này không chạy voice-server, manager, web, ASR, TTS hay 9Router trong
container. MinIO cũng không chạy trừ khi bật profile `object-storage` rõ ràng.

Backend monorepo của Veetee. Realtime voice path và management control plane được
tách riêng nhưng dùng cùng contracts. Các app và model worker chạy thủ công trên
host trong development; `compose.infra.yaml` chỉ cung cấp PostgreSQL/Redis và
profile MinIO tùy chọn.

## Apps

```text
apps/
├── voice-server/     # Python asyncio/FastAPI/WebSocket
├── manager-api/      # NestJS/Fastify/PostgreSQL/Redis
└── manager-web/      # Vue 3/TypeScript/Vite
packages/
├── contracts/        # JSON schema, fixtures, generated types
├── provider-sdk/     # adapter ports/conformance tests
└── ui-tokens/        # color/type/spacing tokens
prototypes/
└── manager-web/      # HTML prototype để duyệt thiết kế
```

## Local ports, không cần domain

| Service | Port | URL mẫu |
|---|---:|---|
| Voice WebSocket | 8000 | `ws://192.168.1.20:8000/veetee/v1/` |
| Manager API | 8002 | `http://192.168.1.20:8002/api/v1` |
| Device edge/bootstrap | 8003 | `http://192.168.1.20:8003/veetee/ota/` |
| Manager Web | 8081 | `http://192.168.1.20:8081` |
| 9Router (internal) | 20128 | `http://127.0.0.1:20128/v1` |

Compatibility aliases `/xiaozhi/v1/` và `/xiaozhi/ota/` được giữ trong gateway/route layer, không lan vào domain logic.

Manager API cũng publish desired config, wake profiles và signed resource bundles; device-edge expose canonical device routes, firmware tự pull/verify/apply theo manifest. Artifact download không đi qua voice WebSocket. Dev có thể proxy port 8003 bằng Caddy/Nginx; không tạo database/config source thứ hai.

Device report resource apply state qua authenticated
`PUT /veetee/devices/:id/reported-state` hoặc alias `/xiaozhi/...`. Sequence cao hơn
advance atomically, cùng sequence là retry không mutate và sequence thấp hơn trả
`409`; contract nằm ở `packages/contracts/fixtures/devices/reported-state-v1.json`.

V1 là single-node deployment: voice-server, manager, 9Router, VAD/ASR/TTS workers
chạy trên cùng máy. Port `20128` chỉ dành cho voice-server nội bộ; không cho ESP32
hoặc LAN gọi trực tiếp.

## Voice WebSocket hiện tại

- Route native `/veetee/v1/` và compatibility `/xiaozhi/v1/` yêu cầu
  `Protocol-Version`, hardware `Device-Id`, `Client-Id` và Bearer token khi device
  auth bật.
- Server chờ và validate exact device hello trước khi trả server hello; timeout mặc
  định 10 giây, control frame tối đa 8 KiB.
- Compatibility audio profile hiện là Opus mono: uplink 16 kHz/60 ms, downlink
  24 kHz/60 ms. Hai hướng có setting riêng để không nhầm sample rate.
- Malformed JSON/hello đóng code `1002`, frame quá lớn `1009`, sai `session_id`
  `1008`. Device event sau handshake luôn phải mang đúng session ID.
- Session vẫn giữ `mode=auto`: `listen:start` mở assistant gate, VAD tự finalize;
  abort cancellation không gửi wire event custom ngoài contract.

## Spec bắt buộc

- `../docs/02-system-architecture.md`
- `../docs/04-protocol-compatibility.md`
- `../docs/06-provider-and-mcp.md`
- `../docs/14-model-and-provider-baseline.md`
- `../docs/07-manager-product-spec.md`
- `../docs/08-roadmap.md`
- `../docs/12-dynamic-config-and-artifacts.md`
