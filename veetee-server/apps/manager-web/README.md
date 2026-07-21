# manager-web

Vue 3/Vite management console. Visual, responsive behavior and interactions are
loaded from the approved prototype at `../../prototypes/manager-web/index.html`;
the prototype remains the source of truth while API-backed sections are migrated
component by component.

```bash
npm run dev --workspace @veetee/manager-web
```

Default Manager API URL is `http://127.0.0.1:8001`. Override it with
`VITE_MANAGER_API_URL` at build/dev time. No domain is required for the LAN-first
profile.
