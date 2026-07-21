# veetee-server

Backend monorepo của Veetee. Realtime voice path và management control plane được tách riêng nhưng dùng cùng contracts.

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

V1 là single-node deployment: voice-server, manager, 9Router, VAD/ASR/TTS workers
chạy trên cùng máy. Port `20128` chỉ dành cho voice-server nội bộ; không cho ESP32
hoặc LAN gọi trực tiếp.

## Spec bắt buộc

- `../docs/02-system-architecture.md`
- `../docs/04-protocol-compatibility.md`
- `../docs/06-provider-and-mcp.md`
- `../docs/14-model-and-provider-baseline.md`
- `../docs/07-manager-product-spec.md`
- `../docs/08-roadmap.md`
- `../docs/12-dynamic-config-and-artifacts.md`
