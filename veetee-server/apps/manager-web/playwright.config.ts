import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const localBrave = "/opt/brave.com/brave/brave";
const executablePath =
  process.env.PLAYWRIGHT_CHROMIUM_PATH ?? (existsSync(localBrave) ? localBrave : undefined);
// Keep E2E isolated from the long-running LAN/Tailscale dev server on 8081.
const webPort = Number(process.env.VEETEE_WEB_E2E_PORT ?? 8082);
const webUrl = `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "./tests",
  timeout: 20_000,
  use: {
    baseURL: webUrl,
    browserName: "chromium",
    headless: true,
    launchOptions: {
      ...(executablePath ? { executablePath } : {}),
      args: ["--no-sandbox"],
    },
  },
  webServer: {
    command: `VITE_MANAGER_API_URL=http://127.0.0.1:8001 npx vite --host 127.0.0.1 --port ${webPort}`,
    url: webUrl,
    reuseExistingServer: true,
  },
});
