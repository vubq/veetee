---
name: release-readiness
description: Assess whether Veetee changes are ready for release by running only the applicable build, test, lint, contract, security and hardware gates. Use before a release decision; never publish, deploy, sign, roll out or flash.
---

# Release Readiness

Build a gate matrix from the changed files; do not run every suite blindly.

## Applicable gates

From `veetee-server/`:

```bash
npm run docs:validate
npm run typecheck
npm test
npm run build
npm run lint:voice
npm run test:voice
npm run firmware:image:test
npm run release:metadata:test
npm run ui-packs:test
```

Add when affected:

- Manager Web critical flows: `VEETEE_WEB_E2E_PORT=18083 npm run test:e2e --workspace @veetee/manager-web`.
- Manager API integration: dedicated `_test` database only.
- Voice wire/local model path: `npm run test:voice:local-e2e` with prerequisites available.
- Firmware: run `esp32-build-check` host tests and ESP-IDF build.
- Protocol/API schema: validate and update shared contract fixtures and migrations.

## Review

- Inspect `git diff` and generated artifacts deliberately.
- Scan changed files for accidentally added keys, tokens, certificates, audio, transcripts and production data without printing matched secrets.
- Check tenant guards, idempotency and audit for Manager mutations.
- Check cancellation/deadline/stale-generation coverage for realtime changes.
- Verify artifact hashes, signatures, compatibility, desired/reported state and rollback paths when relevant.

Report each gate as pass, fail, blocked or not applicable. List physical-board gates separately: voice/audio/display behavior, soak, power-loss, OTA rollback and serial flash. Never run release-producing scripts, sign artifacts, publish, deploy, start rollout or flash without explicit authorization.
