# manager-web

Vue 3/Vite management console. The approved prototype at
`../../prototypes/manager-web/index.html` is a visual reference, not the target
application architecture. New interactive UI must be implemented as Vue
components; auth, devices, agents, providers and MCP data come from Manager API
through a Zod-validated client and TanStack Query cache.

## UI component contract

Reusable primitives live in `src/components/ui`. Form code must compose
`VtField`, `VtInput`, `VtSelect`, `VtTextarea` and `VtButton` instead of adding
page-specific native control styles. They share the same height, spacing,
Vietnamese typography, focus ring, disabled state and error treatment.

Manager screens still rendered from the approved prototype receive the same
`.vt-control` contract through a temporary compatibility enhancer. Remove that
bridge page-by-page as each screen moves to Vue components; do not add new
imperative HTML renderers. `ProviderDialog` is the first Manager workflow moved
off the renderer, and Login uses the same primitives.

```bash
npm run dev --workspace @veetee/manager-web
npm run test:e2e --workspace @veetee/manager-web
```

Default Manager API URL is `http://127.0.0.1:8001`. Override it with
`VITE_MANAGER_API_URL` at build/dev time. No domain is required for the LAN-first
profile.

When the Vite development server is placed behind a trusted HTTPS tunnel, add its
exact public hostname with `VEETEE_WEB_ALLOWED_HOSTS`. Multiple hosts are
comma-separated; do not disable host checking globally. Example:

```bash
VITE_MANAGER_API_URL=https://veetee-dev.example.ts.net:8443 \
VEETEE_WEB_ALLOWED_HOSTS=veetee-dev.example.ts.net \
npm run dev --workspace @veetee/manager-web
```

The production bundle self-hosts the Vietnamese, Latin Extended and Latin
subsets of Be Vietnam Pro at weights 400–700 for both body and display copy.
Import the complete weight CSS, not only `vietnamese-*.css`: Vietnamese words
contain both basic Latin and accented glyphs, so loading only the Vietnamese
subset would mix Be Vietnam Pro with the browser fallback inside one word. The
bundle does not require Google Fonts or another public CDN on the LAN.
Playwright defaults to the local Brave executable and can be overridden with
`PLAYWRIGHT_CHROMIUM_PATH`.

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
