---
name: vue-frontend-review
description: Review Veetee Manager Web Vue 3 and TypeScript changes for component boundaries, state/query handling, runtime contracts, accessibility and local build health. Use for files under veetee-server/apps/manager-web.
---

# Vue Frontend Review

## Read first

- `../../docs/07-manager-product-spec.md`
- `../../docs/12-dynamic-config-and-artifacts.md` when device state or artifacts are involved.
- The closest `Vt*` UI primitive, page, composable, API schema and test.

## Review

1. Keep API access centralized and parse external responses with existing Zod schemas.
2. Use Pinia for auth/client state and TanStack Query for server state; invalidate the narrow cache after mutations.
3. Keep reusable behavior in composables and presentation in Vue SFCs.
4. Reuse `Vt*` UI primitives and preserve the approved visual direction unless redesign is requested.
5. Check keyboard access, focus, labels, dialog semantics, responsive behavior and reduced motion.
6. Use locale keys for new user-facing text and preserve Vietnamese typography.
7. Reject `v-html`, `innerHTML`, selector-driven controllers and copied imperative prototype JavaScript unless a reviewed boundary requires them.
8. For Realtime Lab, preserve one-time auth frames and distinguish browser PCM simulation from device Opus/hardware behavior.

## Validate

From `veetee-server/`:

```bash
npm run typecheck --workspace @veetee/manager-web
npm run test --workspace @veetee/manager-web
npm run build --workspace @veetee/manager-web
```

Run the nearest Vitest file first when possible. Use `playwright-ui-check` for affected user flows. Report results and unresolved runtime or accessibility gaps; do not access production URLs.
