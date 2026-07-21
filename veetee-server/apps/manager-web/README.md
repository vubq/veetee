# manager-web

Vue 3/Vite management console. Visual and responsive behavior remain sourced from
the approved prototype at `../../prototypes/manager-web/index.html`, while auth,
devices, agents, providers and MCP data now come from Manager API through a
Zod-validated client and TanStack Query cache.

```bash
npm run dev --workspace @veetee/manager-web
npm run test:e2e --workspace @veetee/manager-web
```

Default Manager API URL is `http://127.0.0.1:8001`. Override it with
`VITE_MANAGER_API_URL` at build/dev time. No domain is required for the LAN-first
profile.

The production bundle self-hosts the Vietnamese font subsets; it does not require
Google Fonts or another public CDN on the LAN. Playwright defaults to the local
Brave executable and can be overridden with `PLAYWRIGHT_CHROMIUM_PATH`.
