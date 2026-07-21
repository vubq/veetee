import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const localBrave = "/opt/brave.com/brave/brave";
const executablePath =
  process.env.PLAYWRIGHT_CHROMIUM_PATH ?? (existsSync(localBrave) ? localBrave : undefined);

export default defineConfig({
  testDir: "./tests",
  timeout: 20_000,
  use: {
    baseURL: "http://127.0.0.1:8081",
    browserName: "chromium",
    headless: true,
    launchOptions: {
      ...(executablePath ? { executablePath } : {}),
      args: ["--no-sandbox"],
    },
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:8081",
    reuseExistingServer: true,
  },
});
