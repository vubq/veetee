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

The development command performs one clean TypeScript build, then runs the
compiler and Node in watch mode. This keeps Nest decorator metadata identical to
the production build; do not run the Nest entrypoint directly through `tsx`.
`VEETEE_MANAGER_CORS_ORIGIN` accepts a comma-separated origin allowlist. Browser
preflight explicitly permits the Manager REST verbs `GET`, `HEAD`, `POST`, `PUT`
and `PATCH`; keep this list aligned with controller mutations when adding a new verb.

Docker remains available through `npm run infra:up`. Only PostgreSQL/Redis use
containers in that profile; app/model workers run directly on the host.

The device edge now serves rollout-scoped immutable `manifest.json` and
`content.bin` with device Bearer auth, `Device-Id`, exact lengths and HTTP Range
resume without buffering artifact bodies in process memory. A release remains
invisible until its immutable directory contains the signer-created `.complete`
marker. Local signed releases are generated with
`npm run resources:release -- ...` into the ignored artifact root. Remaining
artifact work is admin upload/publish CRUD, object-store URLs, canary orchestration
and reported-state dashboards.
