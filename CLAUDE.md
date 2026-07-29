# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project authority and boundaries

Read `AGENTS.md` first; its hard rules are authoritative. Then read only the task-specific documents linked there and in `README.md`.

Veetee has two owning source trees:

- `veetee-firmware/`: ESP-IDF 6.0.2, C++17, ESP32-S3 N16R8.
- `veetee-server/`: Python realtime voice data plane, NestJS/Fastify management control plane, Vue/Vite operator console, shared contracts and provider SDK.

Both directories under `references/` are read-only comparison sources. Never edit them.

For reboot recovery, full-stack startup and AI handoff, use `docs/21-local-development-runbook.md`.
The documented known-good VieNeu baseline is commit `e1618d7`; always verify the active
branch/commit before starting a runtime process.

## Architecture

Audio travels directly between the ESP32 and `apps/voice-server` over the versioned WebSocket/Opus contract. `apps/manager-api` manages tenants, devices, agents, providers, credentials, artifacts, OTA and desired/reported state; it must not enter the frame-by-frame audio path. `apps/manager-web` is the operator console and calls Manager API, except Realtime Lab, which uses a separately authorized WebSocket to the voice server.

The voice path is broadly: Opus transport -> PCM/VAD -> Vietnamese ASR -> admission and structured planning -> optional MCP -> streaming LLM -> sentence-streaming TTS -> paced Opus downlink. Cancellation tokens, provider deadlines and turn generations prevent stale output after abort.

Firmware owns hardware, provisioning, activation, WebSocket transport, MCP device tools and signed A/B firmware/resource updates. Preserve WebSocket, OTA/bootstrap and MCP wire compatibility unless an explicit versioned migration is part of the task. Only canonical `/veetee/...` product routes ship.

## Server setup and development

Run server commands from `veetee-server/` unless noted:

```bash
npm ci
uv sync --project apps/voice-server --locked --all-groups

npm run infra:up
npm run db:deploy --workspace @veetee/manager-api
npm run env:voice:sync

npm run dev:voice
npm run dev --workspace @veetee/manager-api
npm run dev --workspace @veetee/manager-web
```

`env:voice:sync` installs the required process-wide `OPENBLAS_NUM_THREADS=1` baseline,
and the bare Voice/E2E/benchmark npm commands pin it before `uv` starts Python.
`VEETEE_TTS_THREADS=2` limits ONNX Runtime only; do not remove the OpenBLAS cap or
compensate by increasing TTS threads. Follow
`docs/21-local-development-runbook.md` for the secret-safe effective-env check.

`infra:up` requires an operational Docker daemon. If Docker is unavailable, use the documented host-local infrastructure scripts rather than changing application code.

## Validation commands

Repository-wide TypeScript/docs gates:

```bash
cd veetee-server
npm run typecheck
npm test
npm run build
npm run docs:validate
```

`npm run lint` currently delegates to workspace lint scripts with `--if-present`, but the npm workspaces do not define substantive ESLint scripts. Use TypeScript typecheck as the TypeScript static gate. Voice-server has explicit Ruff and strict MyPy gates:

```bash
npm run lint:voice
npm run test:voice
npm run test:voice:local-e2e
```

Narrow tests:

```bash
# Voice-server single test
uv run --project apps/voice-server pytest \
  apps/voice-server/tests/test_inactivity.py::test_inactivity_goodbye_closes_gate_after_grace

# Manager API single Vitest file
npm exec --workspace @veetee/manager-api -- \
  vitest run src/modules/devices.controller.test.ts

# Manager Web single Vitest file
npm exec --workspace @veetee/manager-web -- \
  vitest run src/smoke.test.ts

# Shared packages
npm exec --workspace @veetee/contracts -- vitest run src/fixtures.test.ts
npm exec --workspace @veetee/provider-sdk -- vitest run src/provider-sdk.test.ts
```

Manager-specific gates:

```bash
npm run typecheck --workspace @veetee/manager-api
npm run test --workspace @veetee/manager-api
npm run build --workspace @veetee/manager-api
npm run test:integration --workspace @veetee/manager-api

npm run typecheck --workspace @veetee/manager-web
npm run test --workspace @veetee/manager-web
npm run build --workspace @veetee/manager-web
VEETEE_WEB_E2E_PORT=18083 npm run test:e2e --workspace @veetee/manager-web
```

Manager API integration tests require a dedicated database URL ending in `_test`. A single UI scenario can be selected with:

```bash
npm exec --workspace @veetee/manager-web -- \
  playwright test tests/manager.spec.ts -g "<test title>"
```

Release metadata checks that do not publish:

```bash
npm run firmware:image:test
npm run release:metadata:test
npm run ui-packs:test
```

## Firmware build and tests

```bash
source /home/vubq/.espressif/v6.0.2/esp-idf/export.sh
cd veetee-firmware
idf.py set-target esp32s3
idf.py build

cmake -S tests -B build/host-tests
cmake --build build/host-tests
ctest --test-dir build/host-tests --output-on-failure
ctest --test-dir build/host-tests -R '^state_machine_test$' --output-on-failure
```

Do not run `idf.py flash`, `monitor`, change board target or change the provisional pin map unless the user explicitly requests it. A successful build or host test is not physical hardware validation; report board checks separately.

## Working rules

- Use English for code and identifiers. User-facing documentation, explanations and logs may use Vietnamese where appropriate.
- Before changing several files, state a short plan and identify the owning subsystem and compatibility constraints.
- Prefer one small vertical slice with a focused test or fixture. Do not perform unrelated cleanup or wholesale ports.
- Run the narrowest relevant validation after each change group, then broaden only when the affected boundary warrants it.
- Update contracts/fixtures and migration/version metadata before changing a public API, protocol, database, NVS or provider schema.
- Do not hand-edit generated output unless the task specifically owns its generator.
- Do not modify secrets, credentials or production data. Never print them in commands or reports.
- Do not commit, push, publish, deploy, release or flash unless explicitly requested.

## Project skills

Use the task-specific skills under `skills/` when applicable:

- `veetee-development`: project-wide routing and compatibility constraints.
- `voice-server-review`: realtime Python/provider/conversation review.
- `vue-frontend-review`: Manager Web Vue/TypeScript review.
- `playwright-ui-check`: local deterministic UI validation.
- `esp32-build-check`: firmware host and ESP-IDF build validation.
- `safe-refactor`: incremental compatibility-preserving refactors.
- `release-readiness`: non-publishing release gate assessment.
