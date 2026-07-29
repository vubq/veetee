import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const portalRoot = resolve(
  __dirname,
  "../../../../veetee-firmware/main/network/portal",
);

const assets = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/portal.css", ["portal.css", "text/css; charset=utf-8"]],
  ["/portal-en.js", ["portal-en.js", "application/javascript; charset=utf-8"]],
  ["/portal-i18n.js", ["portal-i18n.js", "application/javascript; charset=utf-8"]],
  ["/portal-ui.js", ["portal-ui.js", "application/javascript; charset=utf-8"]],
  ["/portal.js", ["portal.js", "application/javascript; charset=utf-8"]],
] as const);

interface PortalMock {
  locale?: string;
  provision?: Array<{ status: number; body: Record<string, unknown> }>;
  status?: Array<Record<string, unknown>>;
}

async function mockPortal(page: Page, mock: PortalMock = {}): Promise<void> {
  let provisionIndex = 0;
  let statusIndex = 0;
  await page.route("http://portal.veetee/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const asset = assets.get(url.pathname as keyof typeof assets);
    if (asset) {
      return route.fulfill({
        status: 200,
        contentType: asset[1],
        body: readFileSync(resolve(portalRoot, asset[0])),
      });
    }
    if (url.pathname === "/api/config") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ssid: "",
          bootstrap_url: "http://192.168.1.10:8001/veetee/ota/",
          locale: mock.locale ?? "vi-VN",
          time_zone: "Asia/Bangkok",
          wake_profile: "",
        }),
      });
    }
    if (url.pathname === "/api/scan") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { ssid: "Veetee Lab", rssi: -48, secure: true, saved: false },
          { ssid: "Saved Wi-Fi", rssi: -66, secure: true, saved: true },
          { ssid: "Open Guest", rssi: -78, secure: false, saved: false },
        ]),
      });
    }
    if (url.pathname === "/api/provision") {
      const response = mock.provision?.[provisionIndex++] ?? {
        status: 200,
        body: { message: "saved", attempt_id: 7 },
      };
      return route.fulfill({
        status: response.status,
        contentType: "application/json",
        body: JSON.stringify(response.body),
      });
    }
    if (url.pathname === "/api/status") {
      const responses = mock.status ?? [
        { version: 1, attempt_id: 7, phase: "connecting", retryable: false },
        { version: 1, attempt_id: 7, phase: "connected", retryable: false },
      ];
      const response = responses[Math.min(statusIndex++, responses.length - 1)]!;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(response),
      });
    }
    return route.fulfill({ status: 404, body: "Not found" });
  });
}

test("runs the exact portal assets through DHCP success", async ({ page }) => {
  await mockPortal(page, { locale: "unsupported-locale" });
  await page.goto("http://portal.veetee/");

  await expect(page).toHaveTitle("Thiết lập Veetee");
  await expect(page.locator("html")).toHaveAttribute("lang", "vi");
  await expect(page.getByPlaceholder("Wi-Fi hoặc mạng ẩn")).toBeVisible();
  await page.getByRole("button", { name: /Veetee Lab/ }).click();
  await expect(page.locator("#password")).toBeFocused();
  await page.locator("#password").fill("not-a-real-password");
  await page.getByRole("button", { name: "Kết nối Wi-Fi" }).click();

  await expect(page.locator("#password")).toHaveValue("");
  await expect(page.getByRole("status").filter({ hasText: "Veetee đã vào mạng" })).toBeVisible();
  await expect(page.locator("#success")).toBeFocused();
  await expect(page.locator("#success")).toContainText("mã 6 số");
  await expect(page.locator("#success")).not.toContainText(/https?:\/\//);
});

test("localizes validation and server failures in English", async ({ page }) => {
  await mockPortal(page, {
    locale: "en-US",
    provision: [{ status: 409, body: { code: "setup_busy", message: "Vietnamese compatibility message" } }],
  });
  await page.goto("http://portal.veetee/");

  await expect(page).toHaveTitle("Veetee setup");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByPlaceholder("Wi-Fi or hidden network")).toBeVisible();
  await expect(page.getByRole("button", { name: "Show Wi-Fi password" })).toHaveAttribute("aria-controls", "password");

  await page.getByLabel("Network name").fill("Hidden Wi-Fi");
  await page.getByText("Advanced settings", { exact: true }).click();
  await page.getByLabel("IANA time zone").fill("");
  await page.getByRole("button", { name: "Connect Wi-Fi" }).click();
  await expect(page.getByLabel("IANA time zone")).toBeFocused();
  await expect(page.locator("#timeZoneError")).toHaveText("Enter a valid IANA time zone.");

  await page.getByLabel("IANA time zone").fill("Asia/Bangkok");
  await page.getByRole("button", { name: "Connect Wi-Fi" }).click();
  await expect(page.getByRole("alert")).toHaveText(/still trying the previous Wi-Fi setup/i);
  await expect(page.getByRole("alert")).not.toContainText("Vietnamese compatibility message");
});

test("keeps mobile layouts bounded in dark reduced-motion mode", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await mockPortal(page);
  for (const width of [320, 360, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("http://portal.veetee/");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--canvas").trim())).toBe("#0d1719");
    await expect(page.locator(".network").first()).toBeVisible();
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.querySelector(".network")!).transitionDuration)).toBe("0s");
  }
});
