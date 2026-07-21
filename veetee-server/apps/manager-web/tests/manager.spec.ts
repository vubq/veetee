import { expect, test, type Page } from "@playwright/test";

const principal = {
  userId: "user-1",
  tenantId: "tenant-1",
  tenantSlug: "veetee-local",
  role: "OWNER",
  email: "owner@veetee.local",
  displayName: "Veetee Owner",
};

async function mockManagerApi(page: Page): Promise<void> {
  let providerHealth = "unknown";
  await page.route("http://127.0.0.1:8001/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.pathname === "/api/v1/auth/refresh") {
      return json({ code: "unauthorized", message: "No refresh session" }, 401);
    }
    if (url.pathname === "/api/v1/auth/login") {
      return json({
        accessToken: "test-access-token",
        tokenType: "Bearer",
        expiresIn: 900,
        principal,
      });
    }
    if (url.pathname === "/health/ready") {
      return json({ status: "ready", components: { database: "ok", redis: "ok" } });
    }
    if (url.pathname === "/api/v1/devices") return json([]);
    if (url.pathname === "/api/v1/agents") {
      return json([
        {
          id: "agent-1",
          name: "Veetee Việt",
          defaultLocale: "vi-VN",
          interactionMode: "auto",
          persona: "Robot AI thân thiện và rõ ràng.",
          draftConfig: { conversation: { betweenTurnsSeconds: 30 } },
          version: 1,
          publishedVersion: 1,
        },
      ]);
    }
    if (url.pathname === "/api/v1/providers/llm-1/test") {
      providerHealth = "healthy";
      return json({
        id: "llm-1",
        kind: "llm",
        adapter: "openai-compatible-9router",
        model: "cx/gpt-5.6-terra",
        baseUrl: "http://127.0.0.1:20128/v1",
        secretConfigured: true,
        enabled: true,
        health: providerHealth,
      });
    }
    if (url.pathname === "/api/v1/providers") {
      return json([
        {
          id: "llm-1",
          kind: "llm",
          adapter: "openai-compatible-9router",
          model: "cx/gpt-5.6-terra",
          baseUrl: "http://127.0.0.1:20128/v1",
          secretConfigured: true,
          enabled: true,
          health: providerHealth,
        },
      ]);
    }
    if (url.pathname === "/api/v1/mcp/tools") {
      return json([
        {
          name: "self.get_device_status",
          description: "Read current device status.",
          inputSchema: { type: "object", properties: {} },
          audience: "regular",
        },
      ]);
    }
    return json({ code: "not_found", message: "Not found" }, 404);
  });
}

test("logs in and renders API-backed control room", async ({ page }) => {
  await mockManagerApi(page);
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();

  await expect(page.locator(".profile-card b")).toHaveText("Veetee Owner");
  await expect(page.locator(".agent-poster h2")).toHaveText("Veetee Việt");
  await expect(page.locator(".release-hero")).toContainText("CHƯA CÓ ARTIFACT");

  await page.locator('[data-page-link="providers"]').first().click();
  await expect(page.locator(".provider-table")).toContainText("cx/gpt-5.6-terra");
  await page.getByRole("button", { name: "Test" }).click();
  await expect(page.locator(".provider-row").nth(1)).toContainText("healthy");
});

test("keeps the approved mobile navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockManagerApi(page);
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();
  await expect(page.locator(".primary-nav")).toBeVisible();
  await expect(page.locator(".primary-nav")).toHaveCSS("position", "fixed");
  await expect(page.locator(".mobile-brand")).toBeVisible();
});
