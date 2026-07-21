import { expect, test, type Page } from "@playwright/test";

const principal = {
  userId: "user-1",
  tenantId: "tenant-1",
  tenantSlug: "veetee-local",
  role: "OWNER",
  email: "owner@veetee.local",
  displayName: "Veetee Owner",
};

async function mockManagerApi(
  page: Page,
  options: {
    withDevice?: boolean;
    withConversationEvents?: boolean;
    withResources?: boolean;
    rolloutCalls?: unknown[];
    toolCalls?: unknown[];
    agentPatches?: unknown[];
    providerPatches?: unknown[];
  } = {},
): Promise<void> {
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
    if (url.pathname === "/api/v1/devices") {
      return json(
        options.withDevice
          ? [
              {
                id: "device-1",
                hardwareId: "A1B2C3D4E5F6",
                name: "Veetee Lab",
                status: "online",
                firmwareVersion: "0.1.0",
                desiredState: { version: 2, state: {} },
                reportedState: { version: 2, state: {} },
                pairedAt: "2026-07-22T00:00:00.000Z",
              },
            ]
          : [],
      );
    }
    if (url.pathname === "/api/v1/conversation-events") {
      return json(
        options.withConversationEvents
          ? [
              {
                id: "c3fb2e2f-d2f8-458a-8b3b-65033a106cb3",
                deviceId: "b72559f9-8a1c-47fa-b2af-7c2a85098b2f",
                agentId: "e31b2263-c5b2-43f9-b17d-4046c9703e73",
                sessionId: "session_12345678",
                generation: 1,
                eventType: "listen.start",
                payload: { source: "wake_word" },
                occurredAt: "2026-07-22T03:00:00.000Z",
              },
              {
                id: "232e50eb-857a-4ee7-b4ad-96770f01b560",
                deviceId: "b72559f9-8a1c-47fa-b2af-7c2a85098b2f",
                agentId: "e31b2263-c5b2-43f9-b17d-4046c9703e73",
                sessionId: "session_12345678",
                turnId: "session_12345678:1",
                generation: 2,
                eventType: "stt.final",
                payload: { locale: "vi-VN", character_count: 28, confidence: 0.91 },
                occurredAt: "2026-07-22T03:00:01.200Z",
              },
              {
                id: "1dfc897b-4911-40ef-bc0a-cb242dd00da5",
                deviceId: "b72559f9-8a1c-47fa-b2af-7c2a85098b2f",
                agentId: "e31b2263-c5b2-43f9-b17d-4046c9703e73",
                sessionId: "session_12345678",
                turnId: "session_12345678:1",
                generation: 2,
                eventType: "tts.start",
                payload: {},
                occurredAt: "2026-07-22T03:00:01.650Z",
              },
            ]
          : [],
      );
    }
    if (url.pathname === "/api/v1/artifacts") {
      return json(
        options.withResources
          ? [
              {
                id: "stable",
                kind: "resource_bundle",
                version: "1.0.0",
                channel: "stable",
                sizeBytes: 125943,
                sha256: "56fc71dda4bf4ebe6ed87359e3bda7eebef38dc0b8b01ce1203d2cd1dc212562",
                contentType: "application/vnd.veetee.esp-sr-model-pack",
                runtime: "esp-sr",
                runtimeAbi: 1,
                license: "ESP-SR bring-up model pack",
                board: "veetee-s3-n16r8",
                minFirmware: "0.2.0",
                maxFirmware: "0.3.0",
                signatureKeyId: "veetee-dev-release-2026-01",
                securityEpoch: 1,
                benchmarkStatus: "not_run",
                status: "published",
                publishedAt: "2026-07-22T03:45:00.000Z",
                createdAt: "2026-07-22T03:44:00.000Z",
              },
            ]
          : [],
      );
    }
    if (url.pathname === "/api/v1/wake-profiles") {
      return json(
        options.withResources
          ? [
              {
                id: "a9dc1d82-e265-47cc-a6a0-73f938dcf3b8",
                artifactId: "stable",
                name: "ESP-SR bring-up",
                locale: "vi-VN",
                channel: "development",
                activationPhrase: "Hi ESP",
                activation: {
                  detectorId: "wakenet:hi_esp",
                  sensitivity: 0.5,
                  cooldownMs: 1500,
                  allowedStates: ["standby"],
                },
                interrupt: {
                  detectorId: "multinet:stop",
                  sensitivity: 0.6,
                  cooldownMs: 800,
                  allowedStates: ["thinking", "speaking"],
                },
                version: 2,
                publishedVersion: 2,
                productReady: false,
              },
            ]
          : [],
      );
    }
    if (url.pathname === "/api/v1/resource-rollouts" && request.method() === "GET") {
      return json([]);
    }
    if (url.pathname === "/api/v1/resource-rollouts" && request.method() === "POST") {
      options.rolloutCalls?.push(request.postDataJSON());
      return json([
        {
          id: "cb00e69d-e7c3-4e72-bfd0-37711ec4ebbf",
          deviceId: "b72559f9-8a1c-47fa-b2af-7c2a85098b2f",
          artifactId: "stable",
          wakeProfileVersion: 2,
          status: "active",
          desiredStateVersion: 3,
          createdAt: "2026-07-22T03:50:00.000Z",
        },
      ]);
    }
    if (url.pathname === "/api/v1/agents") {
      return json([
        {
          id: "agent-1",
          name: "Veetee Việt",
          defaultLocale: "vi-VN",
          interactionMode: "auto",
          persona: "Robot AI thân thiện và rõ ràng.",
          draftConfig: {
            conversation: { betweenTurnsSeconds: 30, plannerSeconds: 8 },
            futureExtension: { enabled: true },
            providerChains: [
              { kind: "llm", locale: "en-US", providerIds: ["llm-en-fallback"] },
            ],
          },
          version: 1,
          publishedVersion: 1,
        },
      ]);
    }
    if (url.pathname === "/api/v1/agents/agent-1" && request.method() === "PATCH") {
      const patch = request.postDataJSON();
      options.agentPatches?.push(patch);
      return json({
        id: "agent-1",
        name: patch.name,
        defaultLocale: patch.defaultLocale,
        interactionMode: patch.interactionMode,
        persona: patch.persona,
        draftConfig: patch.draftConfig,
        version: 1,
        publishedVersion: 1,
      });
    }
    if (url.pathname === "/api/v1/agents/agent-1/publish") {
      return json({
        id: "agent-1",
        name: "Veetee Việt",
        defaultLocale: "vi-VN",
        interactionMode: "auto",
        persona: "Robot AI thân thiện và rõ ràng.",
        draftConfig: {},
        version: 2,
        publishedVersion: 2,
      });
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
        priority: 10,
        locales: ["vi-VN"],
        health: providerHealth,
        healthLatencyMs: 420,
        healthCheckedAt: "2026-07-22T04:00:00.000Z",
        circuitState: "closed",
        failureCount: 0,
      });
    }
    if (url.pathname === "/api/v1/providers/llm-1" && request.method() === "PATCH") {
      const patch = request.postDataJSON();
      options.providerPatches?.push(patch);
      return json({
        id: "llm-1",
        kind: "llm",
        adapter: patch.adapter,
        model: patch.model,
        ...(patch.baseUrl ? { baseUrl: patch.baseUrl } : {}),
        secretConfigured: patch.secretAction === "clear" ? false : true,
        enabled: patch.enabled,
        priority: patch.priority,
        locales: patch.locales,
        health: "unknown",
        circuitState: "closed",
        failureCount: 0,
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
          priority: 10,
          locales: ["vi-VN"],
          health: providerHealth,
          circuitState: "closed",
          failureCount: 0,
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
          safetyClass: "read_only",
          requiresConfirmation: false,
        },
      ]);
    }
    if (url.pathname === "/api/v1/devices/device-1/mcp/tools") {
      return json([
        {
          name: "self.audio_speaker.set_volume",
          description: "Set speaker output volume.",
          inputSchema: {
            type: "object",
            additionalProperties: false,
            required: ["volume"],
            properties: {
              volume: { type: "integer", minimum: 0, maximum: 100 },
            },
          },
          audience: "regular",
          safetyClass: "reversible",
          requiresConfirmation: false,
        },
      ]);
    }
    if (
      url.pathname ===
      "/api/v1/devices/device-1/mcp/tools/self.audio_speaker.set_volume/call"
    ) {
      options.toolCalls?.push(request.postDataJSON());
      return json({ tool: "self.audio_speaker.set_volume", result: { isError: false } });
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

test("edits provider routing and rotates secrets without reading the old secret", async ({
  page,
}) => {
  const providerPatches: unknown[] = [];
  await mockManagerApi(page, { providerPatches });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();

  await page.locator('[data-page-link="providers"]').first().click();
  await page.getByRole("button", { name: "Sửa" }).click();
  await page.getByLabel("Priority").fill("20");
  await page.getByLabel("Locales").fill("vi-VN, en-US");
  await page.locator('select[name="secretAction"]').selectOption("rotate");
  await page.getByLabel("Secret mới").fill("new-provider-secret");
  await page.getByRole("button", { name: "Lưu provider" }).click();

  await expect.poll(() => providerPatches).toEqual([
    {
      adapter: "openai-compatible-9router",
      model: "cx/gpt-5.6-terra",
      baseUrl: "http://127.0.0.1:20128/v1",
      enabled: true,
      priority: 20,
      locales: ["vi-VN", "en-US"],
      secretAction: "rotate",
      secret: "new-provider-secret",
    },
  ]);
});

test("builds a live MCP form from the device JSON Schema", async ({ page }) => {
  const toolCalls: unknown[] = [];
  await mockManagerApi(page, { withDevice: true, toolCalls });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();

  await page.locator('[data-page-link="mcp"]').first().click();
  await expect(page.locator(".tool-detail")).toContainText("Live device catalog");
  await page.getByLabel("volume").fill("55");
  await page.getByRole("button", { name: "Chạy trên thiết bị" }).click();

  await expect.poll(() => toolCalls).toEqual([
    { arguments: { volume: 55 }, confirmed: false, timeoutSeconds: 10 },
  ]);
});

test("publishes bounded conversation changes without dropping extension fields", async ({
  page,
}) => {
  const agentPatches: unknown[] = [];
  await mockManagerApi(page, { agentPatches });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();

  await page.locator('[data-page-link="agents"]').first().click();
  await page.getByLabel("Chờ câu đầu (giây)").fill("20");
  await page.getByLabel("Giới hạn phiên (giây)").fill("900");
  await page.getByRole("button", { name: /Publish version/ }).click();

  await expect.poll(() => agentPatches).toHaveLength(1);
  expect(agentPatches[0]).toMatchObject({
    draftConfig: {
      futureExtension: { enabled: true },
      conversation: {
        plannerSeconds: 8,
        firstInputSeconds: 20,
        betweenTurnsSeconds: 30,
        maxSessionSeconds: 900,
      },
    },
  });
  const draftConfig = (agentPatches[0] as { draftConfig: Record<string, unknown> })
    .draftConfig;
  expect(draftConfig.providerChains).toEqual(
    expect.arrayContaining([
      { kind: "llm", locale: "en-US", providerIds: ["llm-en-fallback"] },
    ]),
  );
});

test("renders the redacted realtime timeline from Manager API events", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true, withConversationEvents: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();

  await page.locator('[data-page-link="lab"]').first().click();
  await expect(page.locator("#eventLog")).toContainText("stt.final");
  await expect(page.locator("#eventLog")).toContainText("28 ký tự · transcript đã redact");
  await expect(page.locator(".latency-row")).toContainText("450");
  await expect(page.locator("#interruptButton")).toBeDisabled();
});

test("shows signed wake resources and creates desired rollout without claiming apply", async ({
  page,
}) => {
  const rolloutCalls: unknown[] = [];
  await mockManagerApi(page, { withDevice: true, withResources: true, rolloutCalls });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();

  await page.locator('[data-page-link="ota"]').first().click();
  await expect(page.locator(".release-hero")).toContainText("Resource 1.0.0");
  await expect(page.locator(".release-list")).toContainText("bring-up/not benchmarked");
  await page.getByRole("button", { name: "Rollout →" }).click();
  await expect.poll(() => rolloutCalls).toEqual([
    {
      wakeProfileId: "a9dc1d82-e265-47cc-a6a0-73f938dcf3b8",
      deviceIds: ["device-1"],
    },
  ]);
  await expect(page.locator("#toast")).toContainText("chờ reported state");
});
