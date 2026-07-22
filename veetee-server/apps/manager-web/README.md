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

## Web Device Simulator

Trang Realtime Lab có ba input:

- Text chat: bypass VAD/ASR có nhãn và event rõ, còn admission/LLM/MCP/TTS chạy thật.
- Audio Replay: browser decode WAV/MP3/OGG, mix mono, resample 16 kHz và gửi với pacing realtime.
- Live Mic: dùng AudioWorklet; cần HTTPS hoặc localhost để browser cho phép microphone.

Phiên bắt đầu bằng `POST /api/v1/lab/sessions`, sau đó token dùng một lần được gửi
trong WebSocket auth frame, không nằm trong URL. UI không lưu transcript/audio và
không tuyên bố PCM/browser AEC đã kiểm thử Opus, AEC hoặc loa vật lý của ESP32.

Để E2E không vô tình dùng Manager runtime thật đang ở port 8081, chọn port riêng:

```bash
VEETEE_WEB_E2E_PORT=18083 npm run test:e2e --workspace @veetee/manager-web
```
