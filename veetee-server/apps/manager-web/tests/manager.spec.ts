import { expect, test, type Page } from "@playwright/test";

const principal = {
  userId: "user-1",
  tenantId: "tenant-1",
  tenantSlug: "veetee-local",
  role: "OWNER",
  email: "owner@veetee.local",
  displayName: "Veetee Owner",
};

const deviceId = "b72559f9-8a1c-47fa-b2af-7c2a85098b2f";

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
    uiUploads?: unknown[];
    uiRollouts?: unknown[];
    labSessionCalls?: unknown[];
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
                id: deviceId,
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
    if (url.pathname === "/api/v1/ui-packs/rollouts" && request.method() === "GET") {
      return json([]);
    }
    if (url.pathname === "/api/v1/ui-packs/uploads" && request.method() === "POST") {
      options.uiUploads?.push({
        fileName: request.headers()["x-veetee-file-name"],
        contentType: request.headers()["content-type"],
        bytes: request.postDataBuffer()?.length,
      });
      return json({
        id: "ui-signal-1.0.0",
        kind: "display_assets",
        version: "1.0.0",
        channel: "stable",
        sizeBytes: request.postDataBuffer()?.length ?? 0,
        sha256: "56fc71dda4bf4ebe6ed87359e3bda7eebef38dc0b8b01ce1203d2cd1dc212562",
        contentType: "application/vnd.veetee.ui-pack",
        runtime: "veetee-ui",
        runtimeAbi: 1,
        license: "MIT",
        board: "veetee-s3-n16r8",
        minFirmware: "0.3.0",
        maxFirmware: "0.4.0",
        signatureKeyId: "veetee-dev-release-2026-01",
        securityEpoch: 1,
        benchmarkStatus: "not_run",
        status: "validated",
        createdAt: "2026-07-22T03:44:00.000Z",
      });
    }
    if (url.pathname === "/api/v1/artifacts/ui-signal-1.0.0/publish") {
      return json({
        id: "ui-signal-1.0.0",
        kind: "display_assets",
        version: "1.0.0",
        channel: "stable",
        sizeBytes: 19,
        sha256: "56fc71dda4bf4ebe6ed87359e3bda7eebef38dc0b8b01ce1203d2cd1dc212562",
        contentType: "application/vnd.veetee.ui-pack",
        runtime: "veetee-ui",
        runtimeAbi: 1,
        license: "MIT",
        board: "veetee-s3-n16r8",
        minFirmware: "0.3.0",
        maxFirmware: "0.4.0",
        signatureKeyId: "veetee-dev-release-2026-01",
        securityEpoch: 1,
        benchmarkStatus: "not_run",
        status: "published",
        publishedAt: "2026-07-22T03:45:00.000Z",
        createdAt: "2026-07-22T03:44:00.000Z",
      });
    }
    if (url.pathname === "/api/v1/ui-packs/ui-signal-1.0.0/rollout") {
      options.uiRollouts?.push(request.postDataJSON());
      return json([
        {
          id: "76d98993-d0b3-45a8-a2e7-6f00942c6fd7",
          deviceId,
          artifactId: "ui-signal-1.0.0",
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
    if (url.pathname === "/api/v1/lab/sessions" && request.method() === "POST") {
      options.labSessionCalls?.push(request.postDataJSON());
      return json({
        id: "79baf98d-9cf2-4fc4-a15e-7deec63f502e",
        token: "test-lab-token-that-is-long-enough-for-the-browser-contract-and-never-logged",
        websocketUrl: "ws://127.0.0.1:8000/veetee/lab/v1/",
        expiresAt: "2026-07-22T06:01:30.000Z",
        agent: {
          id: "e31b2263-c5b2-43f9-b17d-4046c9703e73",
          name: "Veetee Việt",
          locale: "vi-VN",
          version: 1,
          interactionMode: "auto",
        },
        inputMode: request.postDataJSON().inputMode,
        mcpMode: request.postDataJSON().mcpMode,
      });
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
    if (url.pathname === `/api/v1/devices/${deviceId}/mcp/tools`) {
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
      `/api/v1/devices/${deviceId}/mcp/tools/self.audio_speaker.set_volume/call`
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
  await expect(page.getByLabel("Email")).toHaveClass(/vt-control/);
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

test("uses one Vietnamese font and a consistent focus treatment for form controls", async ({
  page,
}) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();
  await page.locator('[data-page-link="lab"]').first().click();

  const select = page.locator("#labInputMode");
  await expect(select).toHaveClass(/vt-select/);
  await select.focus();
  const style = await select.evaluate((element) => {
    const computed = getComputedStyle(element);
    return {
      appearance: computed.appearance,
      boxShadow: computed.boxShadow,
      fontFamily: computed.fontFamily,
      outlineStyle: computed.outlineStyle,
    };
  });
  expect(style.fontFamily).toContain("Be Vietnam Pro");
  expect(style.appearance).toBe("none");
  expect(style.outlineStyle).toBe("none");
  expect(style.boxShadow).toContain("rgba(33, 66, 85, 0.12)");
});

test("keeps the approved mobile navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockManagerApi(page);
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();
  const menuToggle = page.locator("[data-mobile-menu]");
  await expect(menuToggle).toBeVisible();
  await expect(page.locator(".primary-nav")).toBeHidden();
  await menuToggle.click();
  await expect(page.locator(".primary-nav")).toBeVisible();
  await expect(page.locator(".primary-nav")).toHaveCSS("position", "fixed");
  await expect(page.locator(".mobile-brand")).toBeVisible();
  await expect(page.locator(".mobile-brand")).toHaveText("manager");
  const navBox = await page.locator(".primary-nav").boundingBox();
  expect(navBox?.x).toBeGreaterThanOrEqual(0);
  expect((navBox?.x ?? 0) + (navBox?.width ?? 0)).toBeLessThanOrEqual(390);
  await expect(page.locator(".nav-item")).toHaveCount(8);
  await page.locator('.nav-item[data-page-link="providers"]').click();
  await expect(page.locator('[data-page="providers"]')).toBeVisible();
  await expect(page.locator(".primary-nav")).toBeHidden();
});

test("shows only the input panel selected in Realtime Lab", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();
  await page.locator('[data-page-link="lab"]').first().click();

  await expect(page.locator("#labTextForm")).toBeVisible();
  await expect(page.locator("#labAudioReplay")).toBeHidden();
  await expect(page.locator("#labLiveMic")).toBeHidden();
  await expect(page.locator("#labDeviceField")).toBeHidden();

  await page.locator("#labInputMode").selectOption("audio_replay");
  await expect(page.locator("#labTextForm")).toBeHidden();
  await expect(page.locator("#labAudioReplay")).toBeVisible();
  await expect(page.locator("#labLiveMic")).toBeHidden();

  await page.locator("#labInputMode").selectOption("live_mic");
  await expect(page.locator("#labAudioReplay")).toBeHidden();
  await expect(page.locator("#labLiveMic")).toBeVisible();

  await page.locator("#labMcpMode").selectOption("selected_device");
  await expect(page.locator("#labDeviceField")).toBeVisible();
});

test("previews all built-in device themes and inspects a UI Pack locally", async ({ page }) => {
  const uiUploads: unknown[] = [];
  const uiRollouts: unknown[] = [];
  await mockManagerApi(page, { withDevice: true, uiUploads, uiRollouts });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();

  await page.locator('[data-page-link="device-ui"]').first().click();
  const preview = page.locator("[data-ui-preview]");
  await expect(preview).toHaveAttribute("data-theme", "signal");
  await expect(page.locator("#uiPreviewName")).toHaveText("01 / Signal");

  await page.locator('[data-ui-theme="monolith"]').click();
  await expect(preview).toHaveAttribute("data-theme", "monolith");
  await expect(page.locator("#uiPreviewName")).toHaveText("02 / Monolith");
  await page.locator('[data-ui-state="pairingLost"]').click();
  await expect(preview).toContainText("Cần kết nối lại.");

  await page.locator("[data-ui-pack-file]").setInputFiles({
    name: "veetee-signal.vtp",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("veetee-ui-pack-test"),
  });
  await expect(page.locator("[data-ui-upload-status]")).toHaveText("Hợp lệ để staging");
  await expect(page.locator("[data-ui-file-name]")).toHaveText("veetee-signal.vtp");
  const action = page.locator("[data-ui-stage-pack]");
  await expect(action).toBeEnabled();
  await action.click();
  await expect(action).toHaveText("Publish UI Pack");
  await action.click();
  await expect(action).toHaveText("Rollout lên thiết bị");
  await expect(action).toBeEnabled();
  await action.click();
  await expect(action).toHaveText("Đã tạo rollout");
  await expect.poll(() => uiUploads).toEqual([
    {
      fileName: "veetee-signal.vtp",
      contentType: "application/vnd.veetee.ui-pack",
      bytes: 19,
    },
  ]);
  await expect.poll(() => uiRollouts).toEqual([{ deviceIds: [deviceId] }]);
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

test("keeps device telemetry on overview and opens a clean Web Device Simulator", async ({
  page,
}) => {
  await mockManagerApi(page, { withDevice: true, withConversationEvents: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();

  await expect(page.locator(".latency-row")).toContainText("450");
  await page.locator('[data-page-link="lab"]').first().click();
  await expect(page.locator("#eventLog")).toContainText("Chưa có phiên đang chạy");
  await expect(page.locator("#labFidelity")).toContainText("Text không giả VAD/ASR");
  await expect(page.locator("#interruptButton")).toBeDisabled();
});

test("runs typed turns through the one-use Lab WebSocket without faking VAD or ASR", async ({
  page,
}) => {
  const labSessionCalls: unknown[] = [];
  await page.routeWebSocket("ws://127.0.0.1:8000/veetee/lab/v1/", (webSocket) => {
    let sessionId = "";
    let elapsed = 0;
    const sendEvent = (event: string, payload: Record<string, unknown> = {}) => {
      elapsed += 25;
      webSocket.send(
        JSON.stringify({
          type: "lab.event",
          session_id: sessionId,
          event,
          elapsed_ms: elapsed,
          generation: 2,
          payload,
        }),
      );
    };
    webSocket.onMessage((message) => {
      if (typeof message !== "string") return;
      const payload = JSON.parse(message);
      if (payload.type === "lab.auth") {
        sessionId = "79baf98d-9cf2-4fc4-a15e-7deec63f502e";
        webSocket.send(
          JSON.stringify({
            type: "lab.hello",
            version: 1,
            session_id: sessionId,
            input_mode: "text",
            mcp_mode: "simulated",
            audio: { output_sample_rate: 24000 },
            fidelity: {
              vad_asr: "bypassed",
              admission_llm_tts: "real",
              device_opus_transport: "not_measured",
            },
          }),
        );
        sendEvent("session.opened", { source: "web_lab" });
        sendEvent("listen.start", { source: "button" });
      } else if (payload.type === "lab.text") {
        sendEvent("vad.bypassed", { reason: "typed_text" });
        sendEvent("asr.bypassed", { reason: "typed_text" });
        sendEvent("stt.final", { text: payload.text, source: "typed_text" });
        if (payload.text === "Âm thanh không rõ") {
          sendEvent("admission.final", {
            disposition: "unclear",
            confidence: 0,
            reason_code: "invalid_model_output",
          });
          return;
        }
        sendEvent("admission.final", { disposition: "accepted", confidence: 0.98 });
        sendEvent("llm.delta", { text: "Xin chào, đây là pipeline thật." });
        sendEvent("tts.start");
        sendEvent("tts.first_audio", { sample_rate: 24000 });
        sendEvent("tts.stop");
        sendEvent("listen.start", { source: "turn_continuation" });
      } else if (payload.type === "lab.abort") {
        sendEvent("abort.complete", { reason: "web_interrupt", duration_ms: 18 });
        sendEvent("listen.start", { source: "interrupt" });
      }
    });
  });
  await mockManagerApi(page, { labSessionCalls });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào control room/ }).click();
  await page.locator('[data-page-link="lab"]').first().click();

  await page.getByRole("button", { name: "Bắt đầu phiên thử" }).click();
  await expect(page.locator("#labState")).toContainText("Đang lắng nghe");
  await page.locator("#labTextInput").fill("Xin chào Veetee");
  await page.getByRole("button", { name: "Gửi lượt nói" }).click();

  await expect(page.locator("#labChat")).toContainText("Xin chào Veetee");
  await expect(page.locator("#labChat")).toContainText("pipeline thật");
  await expect(page.locator("#eventLog")).toContainText("vad.bypassed");
  await expect(page.locator("#eventLog")).toContainText("asr.bypassed");
  await expect(page.locator("#labMetrics")).toContainText("BYPASS");
  await page.locator("#labTextInput").fill("Âm thanh không rõ");
  await page.getByRole("button", { name: "Gửi lượt nói" }).click();
  await expect(page.locator("#labState")).toContainText("đang nghe tiếp");
  await expect(page.locator("#labTextInput")).toBeEnabled();
  await page.locator("#interruptButton").click();
  await expect(page.locator("#labMetrics")).toContainText("18");
  await expect.poll(() => labSessionCalls).toEqual([
    { agentId: "agent-1", inputMode: "text", mcpMode: "simulated" },
  ]);
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
      deviceIds: [deviceId],
    },
  ]);
  await expect(page.locator("#toast")).toContainText("chờ reported state");
});
