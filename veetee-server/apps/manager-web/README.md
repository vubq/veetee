# manager-web

Vue 3/Vite management console. The approved prototype at
`../../prototypes/manager-web/index.html` is a visual reference, not the target
application architecture. New interactive UI must be implemented as Vue
components; auth, devices, agents, providers and MCP data come from Manager API
through a Zod-validated client and TanStack Query cache.

Top-level screens use stable hash links such as `#/devices`, `#/resources` and
`#/operations`. Operations is read-only and exposes the tenant-scoped audit trail,
LAN/Tailscale topology, privacy retention policy and firmware inventory without
pretending that an unsigned firmware release is ready.

## UI component contract

Reusable primitives live in `src/components/ui`. Form code must compose
`VtField`, `VtInput`, `VtSelect`, `VtTextarea` and `VtButton` instead of adding
page-specific native control styles. They share the same height, spacing,
Vietnamese typography, focus ring, disabled state and error treatment.

All Manager screens live in `src/components/pages` and are rendered as Vue SFCs.
The old raw-prototype injection and imperative DOM controllers have been
removed. Do not introduce `v-html`, `innerHTML`, selector-driven event wiring or
standalone prototype JavaScript into the runtime application.

Headless UI owns behavior and accessibility for dialogs, menus, tabs,
transitions and the mobile navigation drawer. Veetee components own the visual
tokens, typography and control states. Native form elements remain wrapped by
the shared `Vt*` components where browser semantics are more appropriate than a
custom widget.

Realtime Lab state and WebSocket/audio behavior live in
`src/composables/useRealtimeLab.ts`; the page component only renders reactive
state and invokes typed actions. API-backed lists remain in TanStack Query, and
mutations always invalidate the relevant cache key instead of rewriting the DOM.

## Device display contract

`DISPLAY SYSTEM / UI PACK` is not a conceptual web mockup. It is a software twin
of the current `veetee-firmware/main/display/st7789_display.cpp` renderer:

- portrait ST7789 canvas at 240x280 using the same RGB565 quantization;
- the same 5x7 bitmap glyphs, operational ASCII copy and activation-code layout;
- the exact 13 firmware state IDs in enum order;
- the three compiled UI ABI 1 compositions: `signal`, `monolith` and `quiet`;
- palettes imported directly from the standard UI Packs in `ui-packs`.

The corresponding contract and Canvas renderer live in `src/device-ui` and
`src/components/device-ui`. `firmware-contract.test.ts` reads the firmware C++
sources and fails when state order, copy, target/ABI, built-in Signal colors or
renderer geometry drift without a matching Web update. Signal remains the
firmware built-in fallback. UI Packs are data only and cannot upload executable
layout code; rotation, panel offset, color order and brightness still require
acceptance on physical hardware.

```bash
npm run dev --workspace @veetee/manager-web
npm run test:e2e --workspace @veetee/manager-web
```

When `VITE_MANAGER_API_URL` is not set, Manager Web targets port `8001` on the
same host used to open the web page. This keeps LAN clients from accidentally
calling their own `127.0.0.1`; override it with `VITE_MANAGER_API_URL` at
build/dev time when the API is on another host. No domain is required for the
LAN-first profile.

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
