---
name: playwright-ui-check
description: Start and validate Veetee Manager Web locally with the repository Playwright suite or Playwright MCP. Use for UI flow, responsive, visual, accessibility or browser runtime checks; never target production.
---

# Playwright UI Check

## Prepare

1. Read `../../veetee-server/apps/manager-web/package.json` and `playwright.config.ts`.
2. Prefer the deterministic project E2E suite over ad hoc browser actions.
3. Use only local/dev URLs and an isolated port. Never browse a production deployment.
4. Confirm the app or Playwright web server is ready before testing.

## Project checks

From `veetee-server/`:

```bash
npm run typecheck --workspace @veetee/manager-web
VEETEE_WEB_E2E_PORT=18083 npm run test:e2e --workspace @veetee/manager-web
```

Select one scenario when the task is narrow:

```bash
npm exec --workspace @veetee/manager-web -- \
  playwright test tests/manager.spec.ts -g "<test title>"
```

## MCP checks

Use Playwright MCP only after a local page is reachable. Check the requested flow, keyboard/focus behavior, viewport and page/console errors. Capture screenshots only when requested or needed to explain a visual defect.

Report URL, viewport, flow, assertions and errors. Browser simulation is not ESP32 display, audio, Opus or hardware validation. Do not install additional browsers unless the existing Chromium cannot launch and the user approves the download.
