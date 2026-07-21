---
name: veetee-development
description: Build, modify, review, or plan the Veetee AI robot platform across ESP32-S3 firmware, realtime voice-server, manager-api, manager-web, provider adapters, MCP tools, OTA, activation, protocol compatibility, Vietnamese localization, tests, and operations. Use when working anywhere in veetee-firmware or veetee-server, converting the manager prototype, implementing a roadmap milestone, or comparing behavior with the read-only Xiaozhi references.
---

# Veetee Development

Build Veetee from its project contracts instead of copying the Xiaozhi repositories wholesale. Preserve the proven wire semantics while keeping firmware hardware scope and backend module ownership narrow.

## Start every task

1. Identify the owning source: `veetee-firmware` or `veetee-server`.
2. Read `../../README.md` and the task-specific docs listed below.
3. Read `references/source-map.md` when comparing with Xiaozhi.
4. Inspect the closest implementation and current tests before editing.
5. State the observable outcome, compatibility constraints, validation, and hardware-only gaps.

## Route by task

### Firmware, board, Wi-Fi, audio or OTA

Read:

- `../../docs/03-firmware-spec.md`
- `../../docs/04-protocol-compatibility.md` for transport/bootstrap changes
- `../../docs/05-realtime-conversation.md` for auto conversation, assistant gate, wake, abort or AEC
- `../../docs/11-ai-first-design-principles.md` for any intent, phrase, behavior or routing logic
- `../../docs/12-dynamic-config-and-artifacts.md` for wake profile/model, assets, device config sync or non-firmware binary delivery
- `references/task-checklists.md`, section Firmware

Keep exactly one board implementation in V1. Do not change the provisional pin map without an explicit hardware decision. Post state changes to the main application task; do not block network callbacks, main loop or audio tasks.

Keep `mode=auto` as the default. Button wake and activation wake word must open the same flow. Speech/VAD finalizes a candidate utterance without requiring another button press, but input admission and semantic relevance must accept it before LLM/MCP. Treat button or interrupt-profile input as immediate abort while processing/speaking; keep manual/PTT behind a compatibility/accessibility option.

Treat dynamic firmware-facing config as immutable desired state. Use a signed manifest and inactive resource slot for wake models/assets; do not push large binaries over the voice WebSocket or load arbitrary executable code from resource bundles.

### Voice-server, providers or realtime

Read:

- `../../docs/02-system-architecture.md`
- `../../docs/04-protocol-compatibility.md`
- `../../docs/05-realtime-conversation.md`
- `../../docs/06-provider-and-mcp.md`
- `../../docs/14-model-and-provider-baseline.md` for concrete Vietnamese ASR/VAD/TTS
  models, 9router contract and benchmark/fallback gates
- `../../docs/11-ai-first-design-principles.md`
- `references/task-checklists.md`, section Voice server

Keep manager-api off the frame-by-frame audio path. Give every provider operation a cancellation token and deadline. Drop late events by `turn_id`/generation even if a vendor SDK ignores cancellation.

Do not implement semantic behavior with exact-string conditions or central provider `if/else` chains. Use structured model/intent output, versioned agent config, registries and deterministic policy validation.

Do not build source-specific audio rules. Implement a general admission contract (`accepted`, `non_actionable`, `not_addressed`, `unclear`, `interrupt`) and benchmark false accept/reject across a varied corpus. Keep inactivity timeout, closing grace and provider/tool deadlines separate.

### Manager API, data or activation

Read:

- `../../docs/02-system-architecture.md`
- `../../docs/07-manager-product-spec.md`
- `../../docs/09-testing-security-operations.md`
- `../../docs/12-dynamic-config-and-artifacts.md` when exposing device config/artifact APIs
- `references/task-checklists.md`, section Manager API

Apply tenant guards, idempotency and audit to every mutation. Treat the six-digit code as a short-lived one-time pairing handle: use CSPRNG, TTL, attempt limit and atomic consume.

Artifact/config publish must validate device capability, size, compatibility, hash, signature and rollout scope. Track desired vs reported state; publish success is not device apply success.

### Manager Web or prototype conversion

Read:

- `../../docs/07-manager-product-spec.md`
- `../../docs/12-dynamic-config-and-artifacts.md` for wake/artifact/effective-state UI
- `../../veetee-server/prototypes/manager-web/README.md` when it exists
- `references/task-checklists.md`, section Manager Web

Preserve the approved visual direction unless the user requests redesign. Use generated API types, locale keys and accessible confirmation for user-only MCP/OTA actions.

### MCP or tools

Read:

- `../../docs/04-protocol-compatibility.md`, section MCP
- `../../docs/06-provider-and-mcp.md`
- `references/source-map.md`, section MCP

Preserve the JSON-RPC envelope, cursor pagination and regular/user-only split. Validate schema/ranges and execute firmware callbacks on the main task.

Only dispatch MCP after admission/intent acceptance. Put MCP calls in the current turn cancellation scope and drop stale results after button/interrupt abort.

### Planning or milestone work

Read:

- `../../docs/08-roadmap.md`
- `../../docs/10-ai-development-playbook.md`
- `../../docs/13-decision-register.md`

Choose the earliest incomplete vertical slice. Do not plan a broad port when a fixture-driven compatibility slice can produce a runnable result.

Do not silently decide an item listed under "Bắt buộc xác nhận trước Phase 0". Use the documented recommended default only when the task explicitly authorizes implementation with assumptions, and report that assumption in the handoff.

## Compatibility rules

- Keep WebSocket hello, JSON event semantics, binary Opus framing, OTA/bootstrap and MCP envelope compatible with `../../docs/04-protocol-compatibility.md`.
- Keep `/xiaozhi/...` as a route alias when compatibility is required; keep native domain logic named Veetee.
- Add or update fixtures in `../../veetee-server/packages/contracts/fixtures/` for contract changes.
- Require a version/migration for NVS, database, provider config or public API schema changes.
- Never edit `../../references/xiaozhi-esp32` or `../../references/xiaozhi-esp32-server`.

## Local deployment assumption

Do not require a purchased domain. Default development to configurable LAN IP/ports. Keep endpoint discovery in OTA/bootstrap so IP, tunnel or future domain changes do not require rebuilding firmware. Require TLS before public internet exposure.

## Finish every task

1. Run the narrowest relevant lint, typecheck, unit and integration tests.
2. Run contract fixtures when protocol/API behavior is touched.
3. Report physical hardware scenarios that still require the ESP32-S3 board.
4. Update docs or ADR when behavior, schema, pin, retention or deployment assumptions change.
5. Summarize outcome, files, tests and residual risk without dumping large files.
