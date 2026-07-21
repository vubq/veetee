# manager-api

NestJS/Fastify control plane backed by PostgreSQL and Redis. The current slice
includes tenant-scoped JWT auth with refresh rotation, Argon2id passwords,
PostgreSQL audit/config/device state, Redis one-time pairing, activation challenge
to device token exchange, provider secret encryption and internal immutable agent
snapshots for voice-server.

```bash
cp apps/manager-api/.env.example apps/manager-api/.env
npm run infra:host:prepare # once, Ubuntu host-local fallback without sudo/Docker daemon
npm run infra:host:up
npm run db:deploy --workspace @veetee/manager-api
npm run dev --workspace @veetee/manager-api
```

Docker remains available through `npm run infra:up`. Only PostgreSQL/Redis use
containers in that profile; app/model workers run directly on the host.

Artifact milestone remains: wake profile CRUD, scoped object-store upload,
manifest hash/signature/ABI validation and resource bundle canary/rollback. The API
must never buffer large artifact bodies in process memory.
