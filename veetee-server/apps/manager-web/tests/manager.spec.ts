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
const remoteMcpEndpoint = {
  id: "4bf15340-3120-4ca3-8368-9a282b60b33e",
  name: "Home tools",
  url: "https://mcp.example.test/mcp",
  transport: "streamable_http",
  enabled: true,
  authType: "bearer",
  secretConfigured: true,
  timeoutSeconds: 10,
  resultMaxBytes: 65536,
  networkPolicy: "public_only",
  allowedHosts: ["mcp.example.test"],
  tools: [
    { name: "weather.current", safetyClass: "read_only", requiresConfirmation: false },
  ],
  health: "healthy",
  healthLatencyMs: 42,
  healthCheckedAt: "2026-07-29T08:00:00.000Z",
  createdAt: "2026-07-29T07:00:00.000Z",
  updatedAt: "2026-07-29T08:00:00.000Z",
};

async function mockManagerApi(
  page: Page,
  options: {
    withDevice?: boolean;
    withConversationEvents?: boolean;
    withResources?: boolean;
    withRollouts?: boolean;
    rolloutCalls?: unknown[];
    wakeProfileCalls?: unknown[];
    toolCalls?: unknown[];
    agentPatches?: unknown[];
    providerPatches?: unknown[];
    uiUploads?: unknown[];
    standardUiStages?: unknown[];
    uiRollouts?: unknown[];
    labSessionCalls?: unknown[];
    agentCreates?: unknown[];
    deviceAgentAssignments?: unknown[];
    firmwareRolloutCalls?: unknown[];
    personalityCreates?: unknown[];
    personalityDeletes?: string[];
    withFirmware?: boolean;
    withSecondAgent?: boolean;
    withTtsCatalog?: boolean;
    withTtsWithoutCatalog?: boolean;
    failRemoteMcpQueries?: boolean;
    failMemoryQueries?: boolean;
    draftMemoryPolicyEnabled?: boolean;
    memoryExportCalls?: unknown[];
    primaryAgentPublishedVersion?: number;
    deviceAgentConfigVersion?: number;
    role?: "OWNER" | "ADMIN" | "OPERATOR" | "VIEWER";
    pairingResponses?: Array<"success" | "expired" | "conflict">;
    pairingCalls?: unknown[];
  } = {},
): Promise<void> {
  let providerHealth = "unknown";
  let assignedDeviceAgentId = "agent-1";
  let desiredDeviceAgentVersion = options.deviceAgentConfigVersion ?? 1;
  let createdAgent: Record<string, unknown> | undefined;
  let customPersonalityPresets: Record<string, unknown>[] = [];
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
        principal: { ...principal, role: options.role ?? principal.role },
      });
    }
    if (url.pathname === "/health/ready") {
      return json({ status: "ready", components: { database: "ok", redis: "ok" } });
    }
    if (url.pathname === "/api/v1/agents/prompt-catalog") {
      return json({
        schemaVersion: 1,
        catalogVersion: 1,
        defaultTemplate: "You are {{agent_name}}. Reply in {{language}}. Role: {{persona}}. Personality: {{personality}}.",
        variables: [
          { name: "agent_name", label: "Tên trợ lý", description: "Tên", required: true, dynamic: false },
          { name: "language", label: "Ngôn ngữ", description: "Ngôn ngữ", required: true, dynamic: false },
          { name: "locale", label: "Locale", description: "Locale", required: false, dynamic: false },
          { name: "persona", label: "Persona", description: "Persona", required: false, dynamic: false },
          { name: "personality", label: "Tính cách", description: "Tính cách", required: false, dynamic: false },
          { name: "response_style", label: "Phong cách", description: "Phong cách", required: false, dynamic: false },
          { name: "user_address", label: "Xưng hô", description: "Xưng hô", required: false, dynamic: false },
          { name: "interaction_mode", label: "Mode", description: "Mode", required: false, dynamic: false },
          { name: "config_version", label: "Version", description: "Version", required: false, dynamic: true },
          { name: "current_date", label: "Date", description: "Date", required: false, dynamic: true },
          { name: "current_time", label: "Time", description: "Time", required: false, dynamic: true },
          { name: "timezone", label: "Timezone", description: "Timezone", required: false, dynamic: false },
          { name: "device_locale", label: "Device locale", description: "Device locale", required: false, dynamic: true },
          { name: "device_timezone", label: "Device timezone", description: "Device timezone", required: false, dynamic: true },
          { name: "device_timezone_offset", label: "Device offset", description: "Device offset", required: false, dynamic: true },
          { name: "available_tools", label: "Tools", description: "Tools", required: false, dynamic: true },
        ],
        personalityPresets: [
          { id: "warm-empathetic", label: "Ấm áp, đồng cảm", summary: "Lắng nghe", accent: "coral", instructions: "Ấm áp và đồng cảm.", builtIn: true, deletable: false },
          { id: "stubborn-reasoned", label: "Ngang bướng có lý", summary: "Có chính kiến", accent: "ember", instructions: "Có chính kiến và nêu lý do.", builtIn: true, deletable: false },
          ...customPersonalityPresets,
        ],
      });
    }
    if (url.pathname === "/api/v1/agents/personality-presets" && request.method() === "POST") {
      const input = request.postDataJSON() as Record<string, unknown>;
      options.personalityCreates?.push(input);
      const preset = {
        id: "custom-personality-1",
        ...input,
        builtIn: false,
        deletable: true,
      };
      customPersonalityPresets = [...customPersonalityPresets, preset];
      return json(preset);
    }
    const personalityDeleteMatch = url.pathname.match(
      /^\/api\/v1\/agents\/personality-presets\/([^/]+)$/,
    );
    if (personalityDeleteMatch && request.method() === "DELETE") {
      const id = decodeURIComponent(personalityDeleteMatch[1]!);
      options.personalityDeletes?.push(id);
      const deleted = customPersonalityPresets.find((preset) => preset.id === id);
      customPersonalityPresets = customPersonalityPresets.filter((preset) => preset.id !== id);
      return json(deleted ?? {
        id,
        label: "Tính cách tùy chỉnh",
        summary: "Đã xóa",
        accent: "coral",
        instructions: "Đã xóa",
        builtIn: false,
        deletable: true,
      });
    }
    if (url.pathname === "/api/v1/operations/profile") {
      return json({
        deployment: {
          mode: "single_node",
          domainRequired: false,
          managerApiUrl: "http://192.168.110.115:8001",
          voiceWebsocketUrl: "ws://192.168.110.115:8000/veetee/v1/",
        },
        privacy: { rawAudioStored: false, transcriptStored: false, conversationEventRetentionDays: 7 },
        security: { deviceScopedTokens: true, signedArtifacts: true, publicTlsRequired: false },
        firmware: { configuredVersion: "0.3.0", releaseConfigured: false, otaRoute: "/veetee/ota/" },
      });
    }
    if (url.pathname === "/api/v1/mcp/endpoints" && request.method() === "GET") {
      if (options.failRemoteMcpQueries) {
        return json({ code: "remote_mcp_unavailable", message: "Remote MCP registry tạm thời không khả dụng." }, 503);
      }
      return json([remoteMcpEndpoint]);
    }
    if (/^\/api\/v1\/agents\/[^/]+\/mcp-endpoints$/.test(url.pathname)) {
      return json({
        items: [
          {
            endpointId: remoteMcpEndpoint.id,
            endpointName: "Home tools",
            enabled: true,
            toolNames: ["weather.current"],
            timeoutSeconds: 10,
            health: "healthy",
          },
        ],
      });
    }
    if (/^\/api\/v1\/agents\/[^/]+\/memory\/messages$/.test(url.pathname)) {
      if (options.failMemoryQueries) {
        return json({ code: "memory_unavailable", message: "Kho memory tạm thời không khả dụng." }, 503);
      }
      return json({ items: [], nextCursor: undefined });
    }
    const memoryExportMatch = url.pathname.match(/^\/api\/v1\/agents\/([^/]+)\/memory\/exports$/);
    if (memoryExportMatch && request.method() === "POST") {
      const input = request.postDataJSON() as { deviceId: string };
      options.memoryExportCalls?.push(input);
      return json({
        version: 1,
        exportedAt: "2026-07-29T11:00:00.000Z",
        agentId: decodeURIComponent(memoryExportMatch[1]!),
        deviceId: input.deviceId,
        messages: [],
        facts: [],
      });
    }
    if (/^\/api\/v1\/agents\/[^/]+\/memory\/facts$/.test(url.pathname)) {
      if (options.failMemoryQueries) {
        return json({ code: "memory_unavailable", message: "Kho memory tạm thời không khả dụng." }, 503);
      }
      return json({ items: [], nextCursor: undefined });
    }
    if (url.pathname === "/api/v1/audit-events") {
      return json([
        {
          id: "f8c12a04-4e10-4a5f-a7a8-f522b8b51ac4",
          action: "device.pair",
          targetType: "device",
          targetId: deviceId,
          requestId: "req_12345678",
          details: { hardwareId: "A1B2C3D4E5F6" },
          actorName: "Veetee Owner",
          createdAt: "2026-07-22T04:00:00.000Z",
        },
      ]);
    }
    const pairingMatch = url.pathname.match(
      /^\/api\/v1\/devices\/activation\/(\d{6})\/bind$/,
    );
    if (pairingMatch && request.method() === "POST") {
      options.pairingCalls?.push({ code: pairingMatch[1], body: request.postDataJSON() });
      const response = options.pairingResponses?.shift() ?? "success";
      if (response === "expired") {
        return json(
          { code: "bad_request", message: "Invalid or expired pairing code", request_id: "req_expired" },
          400,
        );
      }
      if (response === "conflict") {
        return json(
          { code: "pairing_conflict", message: "Device already paired", request_id: "req_conflict" },
          409,
        );
      }
      return json({
        id: deviceId,
        hardwareId: "A1B2C3D4E5F6",
        name: request.postDataJSON().name,
        status: "offline",
        agentId: request.postDataJSON().agentId,
        desiredState: { version: 1, state: {} },
        reportedState: { version: 0, state: {} },
        pairedAt: "2026-07-28T03:00:00.000Z",
      });
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
                agentId: assignedDeviceAgentId,
                firmwareVersion: "0.1.0",
                desiredState: {
                  version: 2,
                  state: assignedDeviceAgentId
                    ? {
                        agentId: assignedDeviceAgentId,
                        agentConfigVersion: desiredDeviceAgentVersion,
                      }
                    : {},
                },
                reportedState: {
                  version: 42,
                  state: {
                    capabilities: {
                      board: "veetee-s3-n16r8",
                      display: {
                        target: "st7789-240x280-rgb565",
                        controller: "st7789",
                        width: 240,
                        height: 280,
                        colorFormat: "rgb565",
                        resourceAbi: 2,
                        uiAbi: 1,
                        slotBytes: 2_097_152,
                        hotReload: true,
                        compositions: ["signal", "monolith", "quiet"],
                      },
                      wake: {
                        runtime: "esp-sr",
                        runtimeAbi: 1,
                        resourceAbi: 1,
                        slotBytes: 2_097_152,
                        sampleRateHz: 16_000,
                        channels: 1,
                        hotReload: true,
                      },
                    },
                  },
                },
                pairedAt: "2026-07-22T00:00:00.000Z",
              },
            ]
          : [],
      );
    }
    if (
      url.pathname === `/api/v1/devices/${deviceId}/agent` &&
      request.method() === "PUT"
    ) {
      const input = request.postDataJSON() as { agentId?: string };
      options.deviceAgentAssignments?.push(input);
      assignedDeviceAgentId = input.agentId ?? "";
      desiredDeviceAgentVersion = assignedDeviceAgentId === "agent-2"
        ? 2
        : (options.primaryAgentPublishedVersion ?? 1);
      return json({
        id: deviceId,
        hardwareId: "A1B2C3D4E5F6",
        name: "Veetee Lab",
        status: "online",
        agentId: assignedDeviceAgentId || undefined,
        firmwareVersion: "0.1.0",
        desiredState: {
          version: 3,
          state: assignedDeviceAgentId
            ? {
                agentId: assignedDeviceAgentId,
                agentConfigVersion: desiredDeviceAgentVersion,
              }
            : {},
        },
        reportedState: { version: 42, state: {} },
        pairedAt: "2026-07-22T00:00:00.000Z",
      });
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
    if (url.pathname === "/api/v1/firmware-releases" && request.method() === "GET") {
      return json(options.withFirmware
        ? [{
            id: "fw-0.4.0",
            version: "0.4.0",
            channel: "stable",
            sizeBytes: 1_532_480,
            sha256: "0123456789abcdef".repeat(4),
            contentType: "application/vnd.veetee.esp32s3-firmware",
            board: "veetee-s3-n16r8",
            signatureKeyId: "veetee-dev-release-2026-01",
            securityEpoch: 1,
            status: "published",
            publishedAt: "2026-07-23T04:00:00.000Z",
            createdAt: "2026-07-23T03:00:00.000Z",
          }]
        : []);
    }
    if (url.pathname === "/api/v1/firmware-rollouts" && request.method() === "GET") {
      return json(options.withFirmware
        ? [{
            id: "rollout-1",
            artifactId: "fw-0.4.0",
            previousArtifactId: "fw-0.3.0",
            channel: "stable",
            percentage: 10,
            canaryDeviceIds: [deviceId],
            status: "running",
            selectedDeviceIds: [deviceId],
            activeDeviceIds: [deviceId],
            failedDeviceIds: [],
            createdAt: "2026-07-23T04:01:00.000Z",
            updatedAt: "2026-07-23T04:02:00.000Z",
          }]
        : []);
    }
    if (url.pathname === "/api/v1/firmware-rollouts" && request.method() === "POST") {
      options.firmwareRolloutCalls?.push({ action: "create", body: request.postDataJSON() });
      return json({
        id: "rollout-created",
        artifactId: "fw-0.4.0",
        channel: "stable",
        percentage: 25,
        canaryDeviceIds: [deviceId],
        status: "running",
        selectedDeviceIds: [deviceId],
        activeDeviceIds: [],
        failedDeviceIds: [],
        createdAt: "2026-07-23T05:00:00.000Z",
        updatedAt: "2026-07-23T05:00:00.000Z",
      });
    }
    const firmwareAction =
      /^\/api\/v1\/firmware-rollouts\/([^/]+)\/(pause|resume|rollback)$/.exec(url.pathname);
    if (firmwareAction && request.method() === "POST") {
      options.firmwareRolloutCalls?.push({
        action: firmwareAction[2],
        id: firmwareAction[1],
        body: request.postDataJSON(),
      });
      return json({
        id: firmwareAction[1],
        artifactId: "fw-0.4.0",
        previousArtifactId: "fw-0.3.0",
        channel: "stable",
        percentage: firmwareAction[2] === "resume" ? 20 : 10,
        canaryDeviceIds: [deviceId],
        status: firmwareAction[2] === "pause"
          ? "paused"
          : firmwareAction[2] === "rollback"
            ? "rolled_back"
            : "running",
        selectedDeviceIds: [deviceId],
        activeDeviceIds: [deviceId],
        failedDeviceIds: [],
        createdAt: "2026-07-23T04:01:00.000Z",
        updatedAt: "2026-07-23T05:00:00.000Z",
      });
    }
    if (url.pathname === "/api/v1/wake-profiles" && request.method() === "POST") {
      const input = request.postDataJSON() as Record<string, unknown>;
      options.wakeProfileCalls?.push(input);
      return json({
        id: "wake-profile-created",
        ...input,
        version: 1,
        publishedVersion: 0,
        productReady: false,
      });
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
                sendWakeAudio: false,
                activation: {
                  detectorId: "wn9s_hiesp",
                  sensitivity: 0.5,
                  cooldownMs: 1500,
                  allowedStates: ["standby"],
                },
                interrupt: null,
                version: 2,
                publishedVersion: 2,
                productReady: false,
              },
            ]
          : [],
      );
    }
    if (url.pathname === "/api/v1/resource-rollouts" && request.method() === "GET") {
      return json(options.withRollouts ? [
        {
          id: "cb00e69d-e7c3-4e72-bfd0-37711ec4ebbf",
          deviceId,
          artifactId: "stable",
          wakeProfileVersion: 2,
          status: "active",
          desiredStateVersion: 3,
          createdAt: "2026-07-22T03:50:00.000Z",
        },
      ] : []);
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
      return json(options.withRollouts ? [
        {
          id: "76d98993-d0b3-45a8-a2e7-6f00942c6fd7",
          deviceId,
          artifactId: "ui-signal-1.0.0",
          status: "complete",
          desiredStateVersion: 4,
          createdAt: "2026-07-22T04:00:00.000Z",
        },
      ] : []);
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
        minFirmware: "0.3.1",
        maxFirmware: "0.4.0",
        signatureKeyId: "veetee-dev-release-2026-01",
        securityEpoch: 1,
        benchmarkStatus: "not_run",
        status: "validated",
        createdAt: "2026-07-22T03:44:00.000Z",
      });
    }
    const standardUiMatch = url.pathname.match(
      /^\/api\/v1\/ui-packs\/standard\/(signal|monolith|quiet)\/stage$/,
    );
    if (standardUiMatch && request.method() === "POST") {
      const theme = standardUiMatch[1]!;
      options.standardUiStages?.push(theme);
      return json({
        id: `ui-${theme}-1.2.0`,
        kind: "display_assets",
        version: "1.2.0",
        channel: "stable",
        sizeBytes: 5108,
        sha256: "56fc71dda4bf4ebe6ed87359e3bda7eebef38dc0b8b01ce1203d2cd1dc212562",
        contentType: "application/vnd.veetee.ui-pack",
        runtime: "veetee-ui",
        runtimeAbi: 1,
        license: "MIT",
        board: "veetee-s3-n16r8",
        minFirmware: "0.3.1",
        maxFirmware: "0.4.0",
        signatureKeyId: "veetee-dev-release-2026-01",
        securityEpoch: 1,
        benchmarkStatus: "not_run",
        status: "validated",
        createdAt: "2026-07-22T03:44:00.000Z",
      });
    }
    const publishUiMatch = url.pathname.match(
      /^\/api\/v1\/artifacts\/(ui-(?:signal|monolith|quiet)-1\.(?:0|1|2)\.0)\/publish$/,
    );
    if (publishUiMatch) {
      return json({
        id: publishUiMatch[1]!,
        kind: "display_assets",
        version: publishUiMatch[1]!.endsWith("1.2.0")
          ? "1.2.0"
          : publishUiMatch[1]!.endsWith("1.1.0")
            ? "1.1.0"
            : "1.0.0",
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
    const rolloutUiMatch = url.pathname.match(
      /^\/api\/v1\/ui-packs\/(ui-(?:signal|monolith|quiet)-1\.(?:0|1|2)\.0)\/rollout$/,
    );
    if (rolloutUiMatch) {
      options.uiRollouts?.push(request.postDataJSON());
      return json([
        {
          id: "76d98993-d0b3-45a8-a2e7-6f00942c6fd7",
          deviceId,
          artifactId: rolloutUiMatch[1]!,
          status: "active",
          desiredStateVersion: 3,
          createdAt: "2026-07-22T03:50:00.000Z",
        },
      ]);
    }
    if (url.pathname === "/api/v1/agents" && request.method() === "POST") {
      const input = request.postDataJSON() as Record<string, unknown>;
      options.agentCreates?.push(input);
      createdAgent = {
        id: "agent-created",
        ...input,
        draftConfig: input.draftConfig ?? {},
        version: 1,
        publishedVersion: 0,
      };
      return json(createdAgent);
    }
    if (url.pathname === "/api/v1/agents" && request.method() === "GET") {
      const agents: Record<string, unknown>[] = [
        {
          id: "agent-1",
          name: "Veetee Việt",
          defaultLocale: "vi-VN",
          interactionMode: "auto",
          persona: "Robot AI thân thiện và rõ ràng.",
          draftConfig: {
            conversation: { betweenTurnsSeconds: 30, plannerSeconds: 8 },
            futureExtension: { enabled: true },
            ...(options.draftMemoryPolicyEnabled
              ? {
                  memoryPolicy: {
                    enabled: true,
                    consent: true,
                    storeMessages: true,
                    storeFacts: false,
                    retentionDays: 14,
                    maxMessages: 12,
                    maxMessageCharacters: 2_000,
                    maxContextCharacters: 8_000,
                    factRetentionDays: 90,
                    maxFacts: 50,
                    maxFactCharacters: 1_000,
                  },
                }
              : {}),
            providerChains: [
              { kind: "llm", locale: "en-US", providerIds: ["llm-en-fallback"] },
              ...(options.withTtsCatalog
                ? [{ kind: "tts", locale: "vi-VN", providerIds: ["tts-1"] }]
                : []),
            ],
            ...(options.withTtsCatalog
              ? {
                  voice: {
                    providerId: "tts-1",
                    voiceId: "Trúc Ly",
                    gender: "male",
                    style: "tin_tuc",
                    rate: 1,
                    pitchHz: 0,
                    volume: 1,
                  },
                }
              : {}),
          },
          version: options.primaryAgentPublishedVersion ?? 1,
          publishedVersion: options.primaryAgentPublishedVersion ?? 1,
          publishedMemoryPolicy: {
            enabled: false,
            consent: false,
            storeMessages: false,
            storeFacts: false,
            retentionDays: 7,
            maxMessages: 12,
            maxMessageCharacters: 2_000,
            maxContextCharacters: 8_000,
            factRetentionDays: 90,
            maxFacts: 50,
            maxFactCharacters: 1_000,
          },
        },
      ];
      if (options.withSecondAgent) {
        agents.push({
          id: "agent-2",
          name: "Veetee Khoa học",
          defaultLocale: "vi-VN",
          interactionMode: "auto",
          persona: "Trợ lý giải thích khoa học ngắn gọn.",
          draftConfig: {},
          version: 2,
          publishedVersion: 2,
        });
      }
      if (createdAgent) agents.push(createdAgent);
      return json(agents);
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
        adapter: "openai-compatible-cliproxyapi",
        model: "gpt-5.6-terra",
        baseUrl: "http://127.0.0.1:8317/v1",
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
    if (url.pathname === "/api/v1/providers/tts-1" && request.method() === "PATCH") {
      const patch = request.postDataJSON();
      options.providerPatches?.push(patch);
      return json({
        id: "tts-1",
        kind: "tts",
        adapter: patch.adapter,
        model: patch.model,
        ...(patch.baseUrl ? { baseUrl: patch.baseUrl } : {}),
        config: patch.config ?? {},
        secretConfigured: false,
        enabled: patch.enabled,
        priority: patch.priority,
        locales: patch.locales,
        health: "unknown",
        circuitState: "closed",
        failureCount: 0,
      });
    }
    if (url.pathname === "/api/v1/providers") {
      const providers: Record<string, unknown>[] = [
        {
          id: "llm-1",
          kind: "llm",
          adapter: "openai-compatible-cliproxyapi",
          model: "gpt-5.6-terra",
          baseUrl: "http://127.0.0.1:8317/v1",
          secretConfigured: true,
          enabled: true,
          priority: 10,
          locales: ["vi-VN"],
          health: providerHealth,
          circuitState: "closed",
          failureCount: 0,
        },
      ];
      if (options.withTtsCatalog || options.withTtsWithoutCatalog) {
        providers.push({
          id: "tts-1",
          kind: "tts",
          adapter: "vieneu-local",
          model: "VieNeu-TTS",
          secretConfigured: false,
          enabled: true,
          priority: 10,
          locales: ["vi-VN"],
          health: "healthy",
          circuitState: "closed",
          failureCount: 0,
          config: options.withTtsCatalog
            ? {
                voice: "Trúc Ly",
                voiceId: "Trúc Ly",
                gender: "male",
                style: "tu_nhien",
                rate: 1,
                volume: 1,
                outputSampleRate: 24000,
                supportsPitch: false,
                styles: [
                  { id: "tu_nhien", label: "Tự nhiên / hội thoại" },
                  { id: "doc_truyen", label: "Đọc truyện" },
                ],
                voices: [
                  { id: "Trúc Ly", label: "Trúc Ly", locale: "vi-VN", gender: "female", style: "tu_nhien" },
                  { id: "Ngọc Linh", label: "Ngọc Linh", locale: "vi-VN", gender: "female", style: "doc_truyen" },
                ],
                futureExtension: { enabled: true },
              }
            : {
                voiceId: "Legacy Voice",
                supportsPitch: false,
                futureExtension: { enabled: true },
              },
        });
      }
      return json(providers);
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
    if (url.pathname === `/api/v1/devices/${deviceId}/diagnostics/health`) {
      const counters = {
        micFrames: 9_200,
        micSamples: 2_944_000,
        micReadErrors: 0,
        micReadTimeouts: 1,
        detectorFrameDrops: 2,
        opusEncodeFailures: 0,
        uplinkDrops: 0,
        playbackQueueDrops: 0,
        playbackQueueHighWater: 3,
        opusDecodeFailures: 0,
        speakerWriteFailures: 0,
      };
      return json({
        schemaVersion: 1,
        device: {
          board: "veetee-s3-n16r8",
          firmwareVersion: "0.3.0",
          state: "listening",
          assistantGateOpen: true,
          uptimeMs: 360_000,
          resetReason: "software",
        },
        memory: {
          internalFreeBytes: 80_000,
          internalMinFreeBytes: 60_000,
          psramFreeBytes: 4_000_000,
          psramMinFreeBytes: 3_500_000,
        },
        network: {
          connected: true,
          rssi: -48,
          ipv4: "192.168.110.237",
          disconnectCount: 2,
          reconnectAttemptCount: 3,
          lastDisconnectReason: 201,
        },
        audio: {
          captureTaskRunning: true,
          playbackTaskRunning: true,
          lifetime: counters,
          diagnostic: {
            state: "not_run",
            sessionId: 0,
            durationSeconds: 0,
            startedMs: 0,
            endsMs: 0,
            pcmFrames: 0,
            sampleCount: 0,
            rms: 0,
            peakAbsolute: 0,
            dcOffset: 0,
            clippedSamples: 0,
            clippingPercent: 0,
            rawAudioStored: false,
            counters: {
              ...counters,
              micFrames: 0,
              micSamples: 0,
              micReadTimeouts: 0,
              detectorFrameDrops: 0,
              playbackQueueHighWater: 0,
            },
          },
        },
        resources: {
          wakeResourceHealthy: true,
          uiPackHealthy: true,
          wakeDroppedFrames: 2,
        },
        tasks: {
          minimumStackFreeBytes: 2_048,
          capture: { expected: true, running: true, stackFreeBytes: 4_096 },
          playback: { expected: true, running: true, stackFreeBytes: 5_120 },
          wake: { expected: true, running: true, stackFreeBytes: 3_072 },
          websocketControl: {
            expected: true,
            running: true,
            stackFreeBytes: 6_144,
          },
        },
      });
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

for (const visual of [
  { name: "desktop-light", width: 1440, height: 1000, theme: "light" },
  { name: "desktop-dark", width: 1440, height: 1000, theme: "dark" },
  { name: "mobile-light", width: 390, height: 844, theme: "light" },
  { name: "mobile-dark", width: 390, height: 844, theme: "dark" },
] as const) {
  test(`matches the ${visual.name} shell baseline`, async ({ page }) => {
    await page.setViewportSize({ width: visual.width, height: visual.height });
    await page.emulateMedia({ reducedMotion: "reduce", colorScheme: visual.theme });
    await page.addInitScript((theme) => localStorage.setItem("veetee.manager.theme", theme), visual.theme);
    await mockManagerApi(page, { withDevice: true, withConversationEvents: true });
    await page.goto("/");
    await page.getByLabel("Email").fill("owner@veetee.local");
    await page.getByLabel("Mật khẩu").fill("test-password");
    await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
    await expect(page.locator('[data-page="overview"]')).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot(`manager-shell-${visual.name}.png`, { fullPage: true });
  });
}

test("preserves hash routing, document titles and route focus", async ({ page }) => {
  await mockManagerApi(page);
  await page.goto("/#/providers");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await expect(page).toHaveURL(/#\/providers$/);
  await expect(page).toHaveTitle("Nhà cung cấp AI · Veetee");
  await expect(page.locator('[data-page-link="providers"]').first()).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#manager-content")).toBeFocused();

  await page.locator('[data-page-link="devices"]').first().click();
  await expect(page).toHaveURL(/#\/devices$/);
  await page.goBack();
  await expect(page).toHaveURL(/#\/providers$/);
});

test("persists Light, System and Dark appearance preferences", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await mockManagerApi(page);
  await page.goto("/");

  const root = page.locator("html");
  await expect(root).toHaveAttribute("data-theme-preference", "system");
  await expect(root).toHaveAttribute("data-theme", "dark");
  const loginTheme = page.locator("[data-theme-selector]");
  await expect(loginTheme.getByRole("radio")).toHaveCount(3);
  await expect(loginTheme.getByRole("radio", { name: /^Hệ thống/ })).toBeChecked();

  await loginTheme.getByRole("radio", { name: /^Sáng/ }).check();
  await expect(root).toHaveAttribute("data-theme-preference", "light");
  await expect(root).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(root).toHaveAttribute("data-theme", "light");

  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.getByRole("button", { name: "Giao diện" }).click();
  const appearanceTheme = page.locator(".appearance-panel [data-theme-selector]");
  await appearanceTheme.getByRole("radio", { name: /^Tối/ }).check();
  await expect(root).toHaveAttribute("data-theme-preference", "dark");
  await expect(root).toHaveAttribute("data-theme", "dark");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("veetee.manager.theme"))).toBe("dark");

  await appearanceTheme.getByRole("radio", { name: /^Hệ thống/ }).check();
  await expect(root).toHaveAttribute("data-theme-preference", "system");
  await expect(root).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(root).toHaveAttribute("data-theme", "light");
});

test("pairs a device and explains expired and conflicting codes", async ({ page }) => {
  const pairingCalls: unknown[] = [];
  await mockManagerApi(page, {
    pairingCalls,
    pairingResponses: ["expired", "conflict", "success"],
  });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.getByRole("banner").getByRole("button", { name: "Ghép thiết bị" }).click();
  const dialog = page.getByRole("dialog", { name: "Ghép một Veetee mới" });
  const code = dialog.getByLabel("Mã ghép thiết bị");
  await code.fill("123456");
  await dialog.getByRole("button", { name: "Ghép thiết bị" }).click();
  await expect(dialog).toContainText("không hợp lệ hoặc đã hết hạn");
  await expect(dialog.getByText("Mã yêu cầu: req_expired")).toBeVisible();

  await code.fill("234567");
  await dialog.getByRole("button", { name: "Ghép thiết bị" }).click();
  await expect(dialog).toContainText("workspace khác");

  await code.fill("345678");
  await dialog.getByLabel("Tên thiết bị").fill("Veetee phòng học");
  await dialog.getByRole("button", { name: "Ghép thiết bị" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".vt-toast-region")).toContainText("Đã ghép Veetee phòng học");
  expect(pairingCalls).toEqual([
    { code: "123456", body: { name: "Veetee 56", agentId: "agent-1" } },
    { code: "234567", body: { name: "Veetee 67", agentId: "agent-1" } },
    { code: "345678", body: { name: "Veetee phòng học", agentId: "agent-1" } },
  ]);
});

test("loads a fresh Resources deep link without relying on device cache", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true, withResources: true });
  await page.goto("/#/resources");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await expect(page.locator('[data-page="resources"]')).toBeVisible();
  await expect(page.locator(".page-loading")).toBeHidden();
  await page.getByRole("tab", { name: /Firmware OTA/ }).click();
  await expect(page.getByLabel("Thiết bị canary")).toContainText("Veetee Lab");
});

test("logs in and renders API-backed control room", async ({ page }) => {
  await mockManagerApi(page);
  await page.goto("/");
  await expect(page.getByLabel("Email")).toHaveClass(/vt-control/);
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await expect(page.locator(".profile-button")).toContainText("Veetee Owner");
  await expect(page.locator(".agent-spotlight h3")).toHaveText("Veetee Việt");
  await expect(page.locator(".desktop-nav a")).toHaveCount(9);

  await page.locator('[data-page-link="providers"]').first().click();
  await expect(page.locator(".provider-grid")).toContainText("gpt-5.6-terra");
  await expect(page.locator(".vt-operations-hero")).toContainText("Hệ điều phối AI");
  await expect(page.locator(".vt-metric-strip article")).toHaveCount(3);
  await page.getByRole("button", { name: "Test runtime" }).click();
  await expect(page.locator(".provider-card")).toContainText("Khỏe");
});

test("uses one Vietnamese font and a consistent focus treatment for form controls", async ({
  page,
}) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.locator('[data-page-link="lab"]').first().click();

  const select = page.locator("#labInputMode");
  await expect(page.getByText("Input mode", { exact: true }).locator(".."))
    .toHaveAttribute("for", "labInputMode");
  await expect(select).toHaveAccessibleName("Input mode");
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
  expect(style.boxShadow).toContain("rgba(26, 66, 74, 0.12)");

  await page.evaluate(() => document.fonts.ready);
  const fontResources = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .filter((name) => name.includes("be-vietnam-pro")),
  );
  expect(fontResources.some((name) => name.includes("-latin-"))).toBe(true);
  expect(fontResources.some((name) => name.includes("-vietnamese-"))).toBe(true);
});

test("uses an accessible Headless UI mobile navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockManagerApi(page);
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  const menuToggle = page.locator(".mobile-menu-button");
  await expect(menuToggle).toBeVisible();
  await expect(page.locator(".mobile-nav-panel")).toBeHidden();
  await menuToggle.click();
  const navigationDialog = page.getByRole("dialog", { name: "Điều hướng chính" });
  await expect(navigationDialog).toHaveAccessibleName("Điều hướng chính");
  await expect(page.locator(".mobile-nav-panel")).toBeVisible();
  await expect(navigationDialog.locator(".mobile-nav-panel")).toHaveCSS("position", "fixed");
  await expect(page.locator(".mobile-nav-panel .brand-lockup")).toContainText("veetee");
  await expect.poll(async () => (await page.locator(".mobile-nav-panel").boundingBox())?.x ?? -999).toBeGreaterThanOrEqual(0);
  const navBox = await page.locator(".mobile-nav-panel").boundingBox();
  expect((navBox?.x ?? 0) + (navBox?.width ?? 0)).toBeLessThanOrEqual(390);
  await expect(page.locator(".mobile-nav-panel nav a")).toHaveCount(9);
  await page.locator(".mobile-nav-panel nav a").filter({ hasText: "Nhà cung cấp AI" }).click();
  await expect(page.locator('[data-page="providers"]')).toBeVisible();
  await expect(page.locator(".mobile-nav-panel")).toBeHidden();
});

test("renders semantic device delivery and a unified rollout history", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true, withRollouts: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="devices"]').first().click();
  const delivery = page.locator("[data-device-delivery]");
  await expect(delivery).toHaveAttribute("data-delivery-state", "unmanaged");
  await expect(delivery).toContainText("không được dùng để suy ra drift");
  await expect(delivery).toContainText("Desired revision");
  await expect(delivery).toContainText("Report sequence");

  await page.getByRole("tab", { name: /MCP live/ }).click();
  await expect.poll(async () => {
    const columns = await page.locator(".mcp-toolbar.is-embedded").evaluate((element) => getComputedStyle(element).gridTemplateColumns);
    return columns.trim().split(/\s+/).length;
  }).toBe(1);

  await page.getByRole("tab", { name: /Telemetry/ }).click();
  await expect.poll(async () => {
    const columns = await page.locator(".telemetry-toolbar.is-embedded").evaluate((element) => getComputedStyle(element).gridTemplateColumns);
    return columns.trim().split(/\s+/).length;
  }).toBe(2);

  await page.locator('[data-page-link="resources"]').first().click();
  await page.getByRole("tab", { name: /Rollouts/ }).click();
  await expect(page.locator(".rollout-dashboard .vt-operations-hero")).toContainText("Phân phối có xác nhận");
  await expect(page.locator(".rollout-dashboard .vt-metric-strip article")).toHaveCount(3);
  const history = page.locator("[data-rollout-history]");
  await expect(history.locator("article")).toHaveCount(2);
  await expect(history).toContainText("Wake / model");
  await expect(history).toContainText("UI Pack");
  await expect(history).toContainText("Chờ thiết bị");
  await expect(history).toContainText("Đã áp dụng");
});

test("keeps all six device tabs usable without mobile page overflow", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.locator('[data-page-link="devices"]').first().click();
  await page.setViewportSize({ width: 390, height: 844 });

  const tabs = page.locator(".device-tabs");
  await expect(tabs.getByRole("tab")).toHaveCount(6);
  await expect.poll(async () => tabs.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await tabs.getByRole("tab", { name: /Telemetry/ }).click();
  await expect(page.locator('[data-page="telemetry"]')).toBeVisible();
});

test("renders task stack headroom without overflowing desktop or mobile", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.locator('[data-page-link="devices"]').first().click();
  await page.getByRole("tab", { name: /Chẩn đoán/ }).click();

  const headroom = page.locator(".task-headroom-card");
  await expect(headroom).toBeVisible();
  await expect(headroom.locator(".task-headroom-row")).toHaveCount(4);
  await expect(headroom).toContainText("Capture audio");
  await expect(headroom).toContainText("WebSocket control");
  await expect(headroom).toContainText("2 KB stack");
  await expect(headroom.getByText("An toàn", { exact: true })).toHaveCount(5);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true);
  await expect.poll(async () => headroom.locator(".task-headroom-grid").evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )).toBe(1);
});

test("shows only the input panel selected in Realtime Lab", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
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
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="devices"]').first().click();
  await page.getByRole("tab", { name: /Display \/ UI/ }).click();
  const capabilityBadge = page.locator(".capability-gate > .vt-badge");
  const capabilityBadgeBox = await capabilityBadge.boundingBox();
  expect(capabilityBadgeBox?.width ?? 0).toBeGreaterThan(capabilityBadgeBox?.height ?? 0);
  const fileBadge = page.locator("[data-ui-upload-status]");
  const fileBadgeBox = await fileBadge.boundingBox();
  expect(fileBadgeBox?.width ?? 0).toBeGreaterThan(fileBadgeBox?.height ?? 0);
  const preview = page.locator("[data-ui-preview]");
  await expect(preview).toHaveAttribute("data-theme", "signal");
  await expect(page.locator("#uiPreviewName")).toHaveText("01 / Mobile");

  await page.locator('[data-ui-theme="monolith"]').click();
  await expect(preview).toHaveAttribute("data-theme", "monolith");
  await expect(page.locator("#uiPreviewName")).toHaveText("02 / Companion");
  await page.locator('[data-ui-state="pairing_recovery"]').click();
  await expect(preview).toHaveAttribute("data-state", "pairing_recovery");
  await expect(page.locator(".firmware-contract-card")).toContainText("PAIRING LOST");

  await page.locator("[data-ui-pack-file]").setInputFiles({
    name: "veetee-signal.vtp",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("veetee-ui-pack-test"),
  });
  await expect(page.locator("[data-ui-upload-status]")).toHaveText("Hợp lệ để staging");
  await expect(page.locator("[data-ui-file-name]")).toHaveText("veetee-signal.vtp");
  const action = page.locator("[data-ui-stage-pack]");
  await expect(action).toBeEnabled();
  const actionBox = await action.boundingBox();
  const uploadPanelBox = await page.locator(".upload-panel").boundingBox();
  expect(actionBox?.width ?? 0).toBeGreaterThan((uploadPanelBox?.width ?? 0) * 0.8);
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

test("builds, publishes and rolls out the selected standard firmware UI", async ({ page }) => {
  const standardUiStages: unknown[] = [];
  const uiRollouts: unknown[] = [];
  await mockManagerApi(page, { withDevice: true, standardUiStages, uiRollouts });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="devices"]').first().click();
  await page.getByRole("tab", { name: /Display \/ UI/ }).click();
  await page.locator('[data-ui-theme="monolith"]').click();
  await page.locator("[data-apply-standard-theme]").click();

  await expect(page.locator(".firmware-contract-card")).toContainText(
    "Đã đặt Companion làm desired UI cho Veetee Lab.",
  );
  await expect.poll(() => standardUiStages).toEqual(["monolith"]);
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
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="providers"]').first().click();
  await page.getByRole("button", { name: "Cấu hình" }).click();
  await page.getByLabel("Priority").fill("20");
  await page.getByLabel("Locales").fill("vi-VN, en-US");
  await page.locator('select[name="secretAction"]').selectOption("rotate");
  await page.getByLabel("Secret mới").fill("new-provider-secret");
  await page.getByRole("button", { name: "Lưu provider" }).click();

  await expect.poll(() => providerPatches).toEqual([
    {
      adapter: "openai-compatible-cliproxyapi",
      model: "gpt-5.6-terra",
      baseUrl: "http://127.0.0.1:8317/v1",
      enabled: true,
      priority: 20,
      locales: ["vi-VN", "en-US"],
      config: {
        completionTokenParameter: "max_tokens",
        maxCompletionTokens: 1024,
        parallelToolCalls: true,
        reasoningEffort: "none",
        responseFormat: "auto",
        streamProseResponse: true,
        temperature: 0.2,
        topP: 0.95,
      },
      secretAction: "rotate",
      secret: "new-provider-secret",
    },
  ]);
});

test("derives the VieNeu provider default style from its catalog voice", async ({ page }) => {
  const providerPatches: unknown[] = [];
  await mockManagerApi(page, { providerPatches, withTtsCatalog: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="providers"]').first().click();
  await page.locator('[data-provider-kind="tts"]').getByRole("button", { name: "Cấu hình" }).click();
  const dialog = page.getByRole("dialog", { name: /Cấu hình Tổng hợp giọng nói/ });
  const voice = dialog.getByRole("combobox", { name: /Voice mặc định/ });
  await expect(voice.locator("option")).toHaveText([
    "Chưa chọn",
    "Trúc Ly · Nữ · Tự nhiên / hội thoại",
    "Ngọc Linh · Nữ · Đọc truyện",
  ]);
  await expect(dialog.getByRole("combobox", { name: "Phong cách mặc định", exact: true })).toHaveCount(0);

  const rate = dialog.getByRole("spinbutton", { name: /Tốc độ/ });
  const [voiceBox, rateBox] = await Promise.all([voice.boundingBox(), rate.boundingBox()]);
  expect(Math.abs((voiceBox?.y ?? 0) - (rateBox?.y ?? 0))).toBeLessThanOrEqual(1);
  expect(Math.abs((voiceBox?.height ?? 0) - (rateBox?.height ?? 0))).toBeLessThanOrEqual(1);

  await voice.selectOption("Ngọc Linh");
  await dialog.getByRole("button", { name: "Lưu provider" }).click();
  await expect.poll(() => providerPatches).toHaveLength(1);
  const config = (providerPatches[0] as { config: Record<string, unknown> }).config;
  expect(config).toEqual({
    voice: "Ngọc Linh",
    voiceId: "Ngọc Linh",
    gender: "female",
    style: "doc_truyen",
    rate: 1,
    volume: 1,
    outputSampleRate: 24000,
    supportsPitch: false,
    styles: [
      { id: "tu_nhien", label: "Tự nhiên / hội thoại" },
      { id: "doc_truyen", label: "Đọc truyện" },
    ],
    voices: [
      { id: "Trúc Ly", label: "Trúc Ly", locale: "vi-VN", gender: "female", style: "tu_nhien" },
      { id: "Ngọc Linh", label: "Ngọc Linh", locale: "vi-VN", gender: "female", style: "doc_truyen" },
    ],
    futureExtension: { enabled: true },
  });
});

test("preserves legacy VieNeu metadata when no voice catalog is available", async ({ page }) => {
  const providerPatches: unknown[] = [];
  await mockManagerApi(page, { providerPatches, withTtsWithoutCatalog: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="providers"]').first().click();
  await page.locator('[data-provider-kind="tts"]').getByRole("button", { name: "Cấu hình" }).click();
  const dialog = page.getByRole("dialog", { name: /Cấu hình Tổng hợp giọng nói/ });
  const voice = dialog.getByRole("textbox", { name: /Voice mặc định/ });
  await expect(voice).toHaveValue("Legacy Voice");
  await expect(dialog.getByRole("combobox", { name: "Phong cách mặc định", exact: true })).toHaveCount(0);

  await voice.fill("Custom Voice");
  await dialog.getByRole("button", { name: "Lưu provider" }).click();
  await expect.poll(() => providerPatches).toHaveLength(1);
  const config = (providerPatches[0] as { config: Record<string, unknown> }).config;
  expect(config).toEqual({
    voice: "Custom Voice",
    voiceId: "Custom Voice",
    supportsPitch: false,
    futureExtension: { enabled: true },
  });
});

test("builds a live MCP form from the device JSON Schema", async ({ page }) => {
  const toolCalls: unknown[] = [];
  await mockManagerApi(page, { withDevice: true, toolCalls });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="devices"]').first().click();
  await page.getByRole("tab", { name: /MCP live/ }).click();
  await expect(page.locator(".mcp-toolbar")).toContainText("Live device catalog");
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
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="agents"]').first().click();
  await page.getByLabel("Chờ hoạt động đầu tiên").fill("20");
  await page.getByLabel("Giới hạn phiên").fill("900");
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
        totalTurnSeconds: 0,
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

test("requires consent and publishes bounded cross-session memory policy", async ({ page }) => {
  const agentPatches: unknown[] = [];
  await mockManagerApi(page, { agentPatches });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="agents"]').first().click();
  await page.locator(".agent-config-nav").getByRole("link", { name: /Bộ nhớ/ }).click();
  await page.getByLabel("Bật bộ nhớ bền vững cho trợ lý này").check();
  await page.getByLabel("Lịch sử message đã hoàn tất").check();
  await page.getByRole("button", { name: /Publish version/ }).click();
  await expect(page.getByText("Cần xác nhận consent trước khi bật bộ nhớ.")).toBeVisible();

  await page.getByLabel("Đã có sự đồng ý lưu dữ liệu hội thoại").check();
  await page.getByLabel("Fact có cấu trúc do planner đề xuất").check();
  await page.getByLabel("Retention message (ngày)").fill("14");
  await page.getByRole("button", { name: /Publish version/ }).click();

  await expect.poll(() => agentPatches).toHaveLength(1);
  expect(agentPatches[0]).toMatchObject({
    draftConfig: {
      memoryPolicy: {
        enabled: true,
        consent: true,
        storeMessages: true,
        storeFacts: true,
        retentionDays: 14,
        maxMessages: 12,
        maxMessageCharacters: 2_000,
        maxContextCharacters: 8_000,
        factRetentionDays: 90,
        maxFacts: 50,
        maxFactCharacters: 1_000,
      },
    },
  });
});

test("shows Remote MCP health and explicit agent tool assignments", async ({ page }) => {
  await mockManagerApi(page);
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="mcp"]').first().click();
  await expect(page.locator('[data-page="mcp"]')).toContainText("Home tools");
  await expect(page.locator('[data-page="mcp"]')).toContainText("weather.current");
  await expect(page.locator(".remote-mcp-detail")).toContainText("Khỏe");
  await expect(page.locator(".remote-mcp-assignment")).toContainText("Lưu assignment");

  const timeout = page.locator("[data-assignment-timeout]");
  await expect(timeout).toHaveValue("10");
  const desktopBox = await timeout.boundingBox();
  expect(desktopBox).not.toBeNull();
  expect(desktopBox!.width).toBeGreaterThan(80);
  expect(desktopBox!.height).toBeGreaterThanOrEqual(44);
  expect(await timeout.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(14);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileBox = await timeout.boundingBox();
  expect(mobileBox).not.toBeNull();
  expect(mobileBox!.width).toBeGreaterThan(80);
  expect(mobileBox!.height).toBeGreaterThanOrEqual(44);
  await page.getByRole("button", { name: "Thêm endpoint" }).click();
  const dialog = page.locator(".vt-dialog");
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox).not.toBeNull();
  expect(dialogBox!.width).toBeLessThanOrEqual(390);
  for (const locator of [
    dialog.locator(".vt-field-label").first(),
    dialog.locator("[data-remote-mcp-url]"),
    dialog.getByRole("button", { name: "Tạo endpoint" }),
  ]) {
    expect(await locator.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(14);
  }
  await page.keyboard.press("Escape");
});

test("opens published bounded memory without exposing draft policy or raw audio", async ({ page }) => {
  const memoryExportCalls: unknown[] = [];
  await mockManagerApi(page, {
    withDevice: true,
    draftMemoryPolicyEnabled: true,
    memoryExportCalls,
  });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="memory"]').first().click();
  await expect(page.locator('[data-page="memory"]')).toContainText("Policy đang tắt");
  await expect(page.locator('[data-page="memory"]')).toContainText("Chưa có message được lưu");
  await expect(page.locator('[data-page="memory"]')).not.toContainText("raw audio payload");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Xuất dữ liệu trong phạm vi" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("veetee-memory-agent-1-2026-07-29.json");
  expect(memoryExportCalls).toEqual([{ deviceId }]);
});

test("does not misreport failed Remote MCP and memory queries as empty data", async ({ page }) => {
  await mockManagerApi(page, {
    withDevice: true,
    failRemoteMcpQueries: true,
    failMemoryQueries: true,
  });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="mcp"]').first().click();
  await expect(page.getByText("Remote MCP registry tạm thời không khả dụng.")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText("Chưa có Remote MCP endpoint")).toHaveCount(0);

  await page.locator('[data-page-link="memory"]').first().click();
  await expect(page.getByText("Kho memory tạm thời không khả dụng.").first()).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText("Chưa có message được lưu")).toHaveCount(0);
});

for (const role of ["VIEWER", "OPERATOR", "ADMIN", "OWNER"] as const) {
  test(`matches Remote MCP and memory controls to the ${role} API role`, async ({ page }) => {
    await mockManagerApi(page, { role, withDevice: true });
    await page.goto("/#/mcp");
    await page.getByLabel("Email").fill("owner@veetee.local");
    await page.getByLabel("Mật khẩu").fill("test-password");
    await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
    await expect(page.locator('[data-page="mcp"]')).toBeVisible();

    const canOperate = role !== "VIEWER";
    const canManage = role === "ADMIN" || role === "OWNER";
    await expect(page.locator("[data-remote-mcp-test]")).toHaveCount(canOperate ? 1 : 0);
    await expect(page.locator("[data-remote-mcp-create]")).toHaveCount(canManage ? 1 : 0);
    await expect(page.locator("[data-remote-mcp-secret]")).toHaveCount(canManage ? 1 : 0);
    await expect(page.locator("[data-remote-mcp-toggle]")).toHaveCount(canManage ? 1 : 0);
    await expect(page.locator("[data-remote-mcp-assignment-save]")).toHaveCount(canManage ? 1 : 0);
    if (canManage) await expect(page.locator("[data-assignment-timeout]")).toBeEnabled();
    else await expect(page.locator("[data-assignment-timeout]")).toBeDisabled();

    const memoryLink = page.locator('.desktop-nav [data-page-link="memory"]');
    await expect(memoryLink).toHaveCount(canOperate ? 1 : 0);
    await page.goto("/#/memory");
    if (canOperate) {
      await expect(page.locator('[data-page="memory"]')).toBeVisible();
      await expect(page).toHaveURL(/#\/memory$/);
    } else {
      await expect(page).toHaveURL(/#\/overview$/);
      await expect(page.getByText("Bộ nhớ hội thoại yêu cầu vai trò Operator trở lên.")).toBeVisible();
    }
  });
}

test("clears Remote MCP secrets after Escape, cancel and successful requests", async ({ page }) => {
  const createBodies: Record<string, unknown>[] = [];
  const patchBodies: Record<string, unknown>[] = [];
  await mockManagerApi(page);
  await page.route(/\/api\/v1\/mcp\/endpoints(?:\/[^/]+)?$/, async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      createBodies.push(body);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...remoteMcpEndpoint,
          ...body,
          id: "7bf15340-3120-4ca3-8368-9a282b60b33e",
          secretConfigured: true,
          health: "unknown",
          updatedAt: "2026-07-29T09:00:00.000Z",
        }),
      });
    }
    if (request.method() === "PATCH") {
      const body = request.postDataJSON() as Record<string, unknown>;
      patchBodies.push(body);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...remoteMcpEndpoint,
          secretConfigured: body.secretAction === "clear" ? false : true,
          enabled: body.secretAction === "clear" ? false : remoteMcpEndpoint.enabled,
          updatedAt: "2026-07-29T09:00:00.000Z",
        }),
      });
    }
    return route.fallback();
  });

  await page.goto("/#/mcp");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.getByRole("button", { name: "Xoay secret" }).click();
  await page.locator("[data-remote-mcp-rotate-secret]").fill("escape-secret");
  await page.keyboard.press("Escape");
  await expect(page.locator(".vt-dialog")).toBeHidden();
  await page.getByRole("button", { name: "Xoay secret" }).click();
  await expect(page.locator("[data-remote-mcp-rotate-secret]")).toHaveValue("");
  await page.locator("[data-remote-mcp-rotate-secret]").fill("cancel-secret");
  await page.locator(".vt-dialog").getByRole("button", { name: "Hủy" }).click();
  await page.getByRole("button", { name: "Xoay secret" }).click();
  await expect(page.locator("[data-remote-mcp-rotate-secret]")).toHaveValue("");
  await page.locator("[data-remote-mcp-rotate-secret]").fill("saved-secret");
  await page.locator(".vt-dialog").getByRole("button", { name: "Áp dụng" }).click();
  await expect.poll(() => patchBodies).toContainEqual({ secretAction: "rotate", secret: "saved-secret" });
  await page.getByRole("button", { name: "Xoay secret" }).click();
  await expect(page.locator("[data-remote-mcp-rotate-secret]")).toHaveValue("");
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Thêm endpoint" }).click();
  let dialog = page.locator(".vt-dialog");
  await dialog.getByLabel("Loại xác thực").selectOption("bearer");
  await dialog.locator("[data-remote-mcp-create-secret]").fill("create-escape-secret");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Thêm endpoint" }).click();
  dialog = page.locator(".vt-dialog");
  await dialog.getByLabel("Loại xác thực").selectOption("bearer");
  await expect(dialog.locator("[data-remote-mcp-create-secret]")).toHaveValue("");
  await dialog.getByLabel("Tên endpoint").fill("Weather tools");
  await dialog.locator("[data-remote-mcp-url]").fill("https://weather.example.test/mcp");
  await dialog.locator("[data-remote-mcp-create-secret]").fill("saved-create-secret");
  await dialog.getByLabel("Tên tool").fill("weather.current");
  await dialog.getByRole("button", { name: "Tạo endpoint" }).click();
  await expect.poll(() => createBodies).toHaveLength(1);
  expect(createBodies[0]).toMatchObject({ secret: "saved-create-secret" });
  await page.getByRole("button", { name: "Thêm endpoint" }).click();
  dialog = page.locator(".vt-dialog");
  await dialog.getByLabel("Loại xác thực").selectOption("bearer");
  await expect(dialog.locator("[data-remote-mcp-create-secret]")).toHaveValue("");
});

test("recovers failed Remote MCP loading through the visible retry action", async ({ page }) => {
  let attempts = 0;
  await mockManagerApi(page);
  await page.route("http://127.0.0.1:8001/api/v1/mcp/endpoints", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    attempts += 1;
    if (attempts <= 2) {
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ code: "remote_mcp_unavailable", message: "Remote MCP đang gián đoạn." }),
      });
    }
    return route.fallback();
  });

  await page.goto("/#/mcp");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await expect(page.getByText("Remote MCP đang gián đoạn.")).toBeVisible();
  await page.locator(".remote-mcp-query-error").getByRole("button", { name: "Thử lại" }).click();
  await expect(page.getByText("Home tools").first()).toBeVisible();
  expect(attempts).toBe(3);
});

test("surfaces and retries a failed memory pagination request", async ({ page }) => {
  let cursorAttempts = 0;
  await mockManagerApi(page, { withDevice: true });
  await page.route(/\/api\/v1\/agents\/agent-1\/memory\/messages(?:\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    if (!url.searchParams.has("cursor")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [{ id: "44444444-4444-4444-8444-444444444444", sessionId: "session-1", turnId: "turn-1", role: "user", content: "Trang đầu", occurredAt: "2026-07-29T09:00:00.000Z", retentionUntil: "2026-08-05T09:00:00.000Z" }],
          nextCursor: "next-page",
        }),
      });
    }
    cursorAttempts += 1;
    if (cursorAttempts === 1) {
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ code: "memory_page_unavailable", message: "Trang bộ nhớ tiếp theo đang gián đoạn." }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [{ id: "55555555-5555-4555-8555-555555555555", sessionId: "session-1", turnId: "turn-2", role: "assistant", content: "Trang thứ hai", occurredAt: "2026-07-29T09:00:01.000Z", retentionUntil: "2026-08-05T09:00:01.000Z" }],
      }),
    });
  });

  await page.goto("/#/memory");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.getByRole("button", { name: "Tải thêm" }).click();
  await expect(page.getByText("Trang bộ nhớ tiếp theo đang gián đoạn.")).toBeVisible();
  await page.getByRole("button", { name: "Thử tải lại" }).click();
  await expect(page.getByText("Trang thứ hai")).toBeVisible();
  expect(cursorAttempts).toBe(2);
});

test("connects Remote MCP picker and memory tabs to accessible state and keyboard controls", async ({ page }) => {
  let releaseEndpoints!: () => void;
  const endpointGate = new Promise<void>((resolve) => { releaseEndpoints = resolve; });
  await mockManagerApi(page, { withDevice: true });
  await page.route("http://127.0.0.1:8001/api/v1/mcp/endpoints", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await endpointGate;
    return route.fallback();
  });
  await page.goto("/#/mcp");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  const loading = page.locator('[data-page="mcp"] .vt-empty-state[role="status"]');
  await expect(loading.first()).toHaveAttribute("aria-live", "polite");
  releaseEndpoints();

  const endpoint = page.locator(".remote-mcp-list button").first();
  await expect(endpoint).toHaveAttribute("aria-pressed", "true");
  await expect(endpoint.getByText("Khỏe")).toHaveClass(/sr-only/);

  await page.goto("/#/memory");
  const messagesTab = page.getByRole("tab", { name: "Lịch sử message" });
  const factsTab = page.getByRole("tab", { name: "Structured facts" });
  await expect(messagesTab).toHaveAttribute("aria-controls", "memory-panel-messages");
  await expect(factsTab).toHaveAttribute("tabindex", "-1");
  await messagesTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(factsTab).toBeFocused();
  await expect(factsTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#memory-panel-facts")).toHaveAttribute("aria-labelledby", "memory-tab-facts");
  await page.keyboard.press("Home");
  await expect(messagesTab).toBeFocused();
  await expect(messagesTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('[data-page="memory"]')).toContainText("7 ngày");
});

test("derives gender and source style from the selected catalog voice", async ({ page }) => {
  const agentPatches: unknown[] = [];
  await mockManagerApi(page, { agentPatches, withTtsCatalog: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="agents"]').first().click();
  const voiceCard = page.locator(".agent-voice-card");
  const voice = voiceCard.getByRole("combobox", { name: "Voice", exact: true });
  await expect(voice.locator("option")).toHaveText([
    "Chưa chọn",
    "Trúc Ly · Nữ · Tự nhiên / hội thoại",
    "Ngọc Linh · Nữ · Đọc truyện",
  ]);
  await expect(voiceCard.getByRole("combobox", { name: "Giới tính", exact: true })).toHaveCount(0);
  await expect(voiceCard.getByRole("combobox", { name: "Phong cách đọc", exact: true })).toHaveCount(0);

  const rate = voiceCard.getByRole("spinbutton", { name: /Tốc độ/ });
  const [voiceBox, rateBox] = await Promise.all([voice.boundingBox(), rate.boundingBox()]);
  expect(Math.abs((voiceBox?.y ?? 0) - (rateBox?.y ?? 0))).toBeLessThanOrEqual(1);
  expect(Math.abs((voiceBox?.height ?? 0) - (rateBox?.height ?? 0))).toBeLessThanOrEqual(1);

  await voice.selectOption("Ngọc Linh");
  await page.getByRole("button", { name: /Publish version/ }).click();

  await expect.poll(() => agentPatches).toHaveLength(1);
  expect(agentPatches[0]).toMatchObject({
    draftConfig: {
      voice: {
        providerId: "tts-1",
        voiceId: "Ngọc Linh",
        gender: "female",
        style: "doc_truyen",
      },
    },
  });
});

test("clears the persisted agent voice when the operator selects not selected", async ({ page }) => {
  const agentPatches: unknown[] = [];
  await mockManagerApi(page, { agentPatches, withTtsCatalog: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="agents"]').first().click();
  await page.locator(".agent-voice-card").getByRole("combobox", { name: "Voice", exact: true }).selectOption("");
  await page.getByRole("button", { name: /Publish version/ }).click();

  await expect.poll(() => agentPatches).toHaveLength(1);
  const draftConfig = (agentPatches[0] as { draftConfig: Record<string, unknown> }).draftConfig;
  expect(draftConfig).not.toHaveProperty("voice");
});

test("loads a legacy provider voiceId as the agent default", async ({ page }) => {
  await mockManagerApi(page, { withTtsWithoutCatalog: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="agents"]').first().click();
  await expect(
    page.locator(".agent-voice-card").getByRole("combobox", { name: "Voice", exact: true }),
  ).toHaveValue("Legacy Voice");
});

test("creates an independent assistant draft from the manager UI", async ({ page }) => {
  const agentCreates: unknown[] = [];
  await mockManagerApi(page, { agentCreates });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="agents"]').first().click();
  await page.getByRole("button", { name: "Tạo trợ lý" }).click();
  const dialog = page.getByRole("dialog", { name: "Tạo trợ lý mới" });
  await dialog.getByLabel("Tên trợ lý").fill("Veetee Khoa học");
  await expect(dialog.getByLabel("Giới thiệu trợ lý")).toHaveValue("");
  await expect(dialog.getByLabel("Ngôn ngữ AI")).toHaveValue("Tiếng Việt");
  await expect(dialog.getByLabel("Preset tính cách")).toHaveValue("");
  await dialog.getByRole("button", { name: "Tạo draft" }).click();

  await expect.poll(() => agentCreates).toEqual([
    {
      name: "Veetee Khoa học",
      defaultLocale: "vi-VN",
      interactionMode: "auto",
      persona: "",
      draftConfig: {
        prompt: {
          schemaVersion: 1,
          template:
            "You are {{agent_name}}. Reply in {{language}}. Role: {{persona}}. Personality: {{personality}}.",
          language: "Tiếng Việt",
          timeZone: expect.any(String),
          timeZoneSource: "device",
          personalityPresetId: "",
          customPersonality: "",
          responseStyle:
            "Tự nhiên, rõ ràng và vừa đủ chi tiết cho một cuộc trò chuyện bằng giọng nói.",
          userAddress: "",
        },
      },
    },
  ]);
  await expect(dialog).toBeHidden();
  await expect(page.locator(".agent-list")).toContainText("Veetee Khoa học");
  await expect(page.locator(".agent-editor")).toContainText("Bản đã xuất bảnv0");
  await expect(page.locator(".agent-editor")).toContainText("Có thay đổi bản nháp");
});

test("keeps the assistant configuration cockpit aligned on desktop and mobile", async ({ page }) => {
  await mockManagerApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.locator('[data-page-link="agents"]').first().click();

  const navigation = page.locator(".agent-config-nav");
  await expect(navigation.getByRole("link")).toHaveCount(5);
  await expect(page.locator(".agent-config-section")).toHaveCount(5);
  await expect(page.locator(".personality-feature")).toContainText("ĐANG CHỌN");
  await page.getByRole("radio", { name: /Không dùng preset/ }).click();
  await expect(page.getByRole("radio", { name: /Không dùng preset/ })).toHaveAttribute("aria-checked", "true");
  await expect(page.locator(".personality-feature")).toHaveCount(0);
  await page.getByRole("radio", { name: /Ấm áp, đồng cảm/ }).click();
  await expect(page.locator(".personality-feature")).toContainText("ĐANG CHỌN");
  await expect(page.locator(".agent-runtime-grid")).toBeVisible();
  const desktopPersonalityCards = await page.locator(".personality-card").evaluateAll((cards) => {
    const top = cards[0]?.getBoundingClientRect().top;
    return {
      firstWidth: cards[0]?.getBoundingClientRect().width ?? 0,
      firstRowCount: cards.filter((card) => Math.abs(card.getBoundingClientRect().top - (top ?? 0)) <= 1).length,
    };
  });
  expect(desktopPersonalityCards.firstWidth).toBeGreaterThanOrEqual(240);
  expect(desktopPersonalityCards.firstRowCount).toBeLessThanOrEqual(4);
  const personalityCardVisual = await page.locator(".personality-card").first().evaluate((card) => {
    const description = card.querySelector("small");
    const title = card.querySelector("b");
    const cardBox = card.getBoundingClientRect();
    const descriptionBox = description?.getBoundingClientRect();
    return {
      accentRail: getComputedStyle(card, "::after").display,
      titleWhiteSpace: title ? getComputedStyle(title).whiteSpace : "",
      descriptionWhiteSpace: description ? getComputedStyle(description).whiteSpace : "",
      contentBottomGap: descriptionBox ? cardBox.bottom - descriptionBox.bottom : Number.POSITIVE_INFINITY,
    };
  });
  expect(personalityCardVisual.accentRail).toBe("none");
  expect(personalityCardVisual.titleWhiteSpace).toBe("normal");
  expect(personalityCardVisual.descriptionWhiteSpace).toBe("normal");
  expect(personalityCardVisual.contentBottomGap).toBeLessThanOrEqual(16);
  const desktopOverflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  expect(desktopOverflow.documentWidth).toBeLessThanOrEqual(desktopOverflow.viewportWidth);

  const promptSection = page.locator('[id="agent-prompt"]');
  await expect(promptSection).toHaveCount(1);
  await page.getByRole("link", { name: /Base prompt/ }).click();
  await promptSection.scrollIntoViewIfNeeded();
  await expect(promptSection).toBeInViewport();
  const [draftPaneBox, previewPaneBox] = await Promise.all([
    page.locator(".prompt-workbench-pane").boundingBox(),
    page.locator(".prompt-render-preview").boundingBox(),
  ]);
  expect(draftPaneBox).not.toBeNull();
  expect(previewPaneBox).not.toBeNull();
  expect(Math.abs(draftPaneBox!.height - previewPaneBox!.height)).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 1100, height: 900 });
  const intermediateCards = await page.locator(".personality-card").evaluateAll((cards) =>
    cards.slice(0, 2).map((card) => card.getBoundingClientRect().width),
  );
  expect(intermediateCards).toHaveLength(2);
  expect(Math.min(...intermediateCards)).toBeGreaterThanOrEqual(240);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileOverflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  expect(mobileOverflow.documentWidth).toBeLessThanOrEqual(mobileOverflow.viewportWidth);
  const [mobilePersonalityGridBox, mobilePersonalityCardBox] = await Promise.all([
    page.locator(".personality-grid").boundingBox(),
    page.locator(".personality-card").first().boundingBox(),
  ]);
  expect(mobilePersonalityGridBox).not.toBeNull();
  expect(mobilePersonalityCardBox).not.toBeNull();
  expect(Math.abs(mobilePersonalityGridBox!.width - mobilePersonalityCardBox!.width)).toBeLessThanOrEqual(1);
  await expect(page.locator(".agent-config-nav")).toBeVisible();
  await expect(page.locator(".sticky-publish")).toContainText("Publish tạo version mới");
});

test("creates and removes a custom personality without exposing built-in delete actions", async ({ page }) => {
  const personalityCreates: unknown[] = [];
  const personalityDeletes: string[] = [];
  await mockManagerApi(page, { personalityCreates, personalityDeletes });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.locator('[data-page-link="agents"]').first().click();

  await expect(page.locator(".personality-delete")).toHaveCount(0);
  await page.getByTestId("create-personality").click();
  const dialog = page.getByRole("dialog", { name: "Thêm tính cách riêng" });
  await dialog.getByLabel("Tên tính cách").fill("Cà khịa vui");
  await dialog.getByLabel("Mô tả ngắn").fill("Trêu nhẹ, sắc nhưng biết dừng.");
  await dialog.getByRole("radio", { name: "Biển" }).click();
  await expect(dialog.getByRole("radio", { name: "Biển" })).toHaveAttribute("aria-checked", "true");
  await dialog.getByLabel("Hướng dẫn cho AI").fill("Trêu nhẹ theo ngữ cảnh, phản biện lập luận.");
  const personalityPreview = dialog.locator(".personality-create-preview");
  await expect(personalityPreview).toContainText("Cà khịa vui");
  await expect(personalityPreview).toContainText("Trêu nhẹ, sắc nhưng biết dừng.");
  await expect(personalityPreview.locator(".personality-selected")).toBeVisible();
  const [previewSurface, activeCardSurface] = await Promise.all([
    personalityPreview.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        backgroundColor: style.backgroundColor,
        borderColor: style.borderColor,
        borderRadius: style.borderRadius,
        boxShadow: style.boxShadow,
      };
    }),
    page.locator(".personality-card.active").evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        backgroundColor: style.backgroundColor,
        borderColor: style.borderColor,
        borderRadius: style.borderRadius,
        boxShadow: style.boxShadow,
      };
    }),
  ]);
  expect(previewSurface).toEqual(activeCardSurface);
  await dialog.getByRole("button", { name: "Lưu tính cách" }).click();
  await expect.poll(() => personalityCreates).toEqual([
    {
      label: "Cà khịa vui",
      summary: "Trêu nhẹ, sắc nhưng biết dừng.",
      accent: "cyan",
      instructions: "Trêu nhẹ theo ngữ cảnh, phản biện lập luận.",
    },
  ]);
  await expect(page.getByRole("radio", { name: /Cà khịa vui/ })).toBeVisible();
  await expect(page.locator(".personality-delete")).toHaveCount(1);
  await page.locator(".personality-delete").click();
  const confirm = page.getByRole("dialog", { name: "Xóa tính cách riêng?" });
  await confirm.getByRole("button", { name: "Xóa preset" }).click();
  await expect.poll(() => personalityDeletes).toEqual(["custom-personality-1"]);
  await expect(page.getByRole("radio", { name: /Cà khịa vui/ })).toHaveCount(0);
});

test("changes the published assistant assigned to an existing device", async ({ page }) => {
  const deviceAgentAssignments: unknown[] = [];
  await mockManagerApi(page, {
    withDevice: true,
    withSecondAgent: true,
    deviceAgentAssignments,
  });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="devices"]').first().click();
  await page.getByLabel("Trợ lý cho thiết bị").selectOption("agent-2");
  await page.getByRole("button", { name: "Lưu thay đổi" }).click();

  await expect.poll(() => deviceAgentAssignments).toEqual([{ agentId: "agent-2" }]);
  await expect(page.locator(".vt-toast-region")).toContainText("Đã đổi trợ lý cho thiết bị");
});

test("rolls a newer published version to a device already using the same assistant", async ({
  page,
}) => {
  const deviceAgentAssignments: unknown[] = [];
  await mockManagerApi(page, {
    withDevice: true,
    primaryAgentPublishedVersion: 3,
    deviceAgentConfigVersion: 2,
    deviceAgentAssignments,
  });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="devices"]').first().click();
  const updateButton = page.getByRole("button", { name: "Cập nhật v3" });
  await expect(updateButton).toBeEnabled();
  await updateButton.click();

  await expect.poll(() => deviceAgentAssignments).toEqual([{ agentId: "agent-1" }]);
  await expect(page.getByRole("button", { name: "Đã lưu" })).toBeDisabled();
});

test("keeps device assistant controls cohesive on desktop and mobile", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true, withSecondAgent: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.locator('[data-page-link="devices"]').first().click();

  const binding = page.locator(".device-agent-binding");
  const select = page.getByLabel("Trợ lý cho thiết bị");
  const savedButton = page.getByRole("button", { name: "Đã lưu" });
  await expect(binding).toContainText("HỒ SƠ TRỢ LÝ");
  await expect(savedButton).toBeDisabled();

  const desktopSelect = await select.boundingBox();
  const desktopButton = await savedButton.boundingBox();
  expect(desktopSelect).not.toBeNull();
  expect(desktopButton).not.toBeNull();
  expect(Math.abs(desktopSelect!.y - desktopButton!.y)).toBeLessThanOrEqual(4);
  expect(Math.abs(desktopSelect!.height - desktopButton!.height)).toBeLessThanOrEqual(2.5);
  expect(await select.evaluate((element) => getComputedStyle(element).backgroundImage)).toBe("none");
  const desktopOverflow = await binding.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(desktopOverflow.scrollWidth).toBeLessThanOrEqual(desktopOverflow.clientWidth);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileSelect = await select.boundingBox();
  const mobileButton = await savedButton.boundingBox();
  expect(mobileSelect).not.toBeNull();
  expect(mobileButton).not.toBeNull();
  expect(mobileButton!.y).toBeGreaterThan(mobileSelect!.y + mobileSelect!.height);
  expect(Math.abs(mobileSelect!.x - mobileButton!.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(mobileSelect!.width - mobileButton!.width)).toBeLessThanOrEqual(1);
  const mobileOverflow = await binding.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(mobileOverflow.scrollWidth).toBeLessThanOrEqual(mobileOverflow.clientWidth);
});

test("keeps device telemetry on overview and opens a clean Web Device Simulator", async ({
  page,
}) => {
  await mockManagerApi(page, { withDevice: true, withConversationEvents: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await expect(page.getByRole("region", { name: "Tóm tắt trạng thái" }).getByText("ASR → TTS gần nhất").locator("../..")).toContainText("450");
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
            prompt: {
              applied: true,
              version: 2,
              language: "Tiếng Việt",
              personality: "stubborn-reasoned",
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
  await mockManagerApi(page, { labSessionCalls, withSecondAgent: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.locator('[data-page-link="lab"]').first().click();
  await page.locator("#labAgent").selectOption("agent-2");

  await page.getByRole("button", { name: "Bắt đầu phiên thử" }).click();
  await expect(page.locator("#labState")).toContainText("Đang lắng nghe");
  await expect(page.locator("#labPromptSnapshot")).toContainText("Prompt v2 · stubborn-reasoned");
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
  const [consoleBox, controlsBox] = await Promise.all([
    page.locator(".lab-console").boundingBox(),
    page.locator(".lab-controls").boundingBox(),
  ]);
  expect(consoleBox).not.toBeNull();
  expect(controlsBox).not.toBeNull();
  expect(
    Math.abs(
      consoleBox!.y + consoleBox!.height - (controlsBox!.y + controlsBox!.height),
    ),
  ).toBeLessThanOrEqual(1);
  await expect.poll(() => labSessionCalls).toEqual([
    { agentId: "agent-2", inputMode: "text", mcpMode: "simulated" },
  ]);
});

test("keeps wake profiles global but applies them from a compatible online device", async ({
  page,
}) => {
  const rolloutCalls: unknown[] = [];
  const wakeProfileCalls: unknown[] = [];
  await mockManagerApi(page, { withDevice: true, withResources: true, rolloutCalls, wakeProfileCalls });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  await page.locator('[data-page-link="resources"]').first().click();
  await page.getByRole("tab", { name: /Wake profiles/ }).click();
  await expect(page.locator(".wake-list")).toContainText("Hi ESP");
  await expect(page.locator(".wake-list")).toContainText("Chưa benchmark");
  await expect(page.locator(".wake-list")).toContainText("Áp dụng trong Thiết bị");
  await expect(page.locator(".wake-list")).toContainText("Wake audio: Tắt");
  const wakeAudioSwitch = page.getByRole("switch", { name: "Gửi âm thanh trước wake word" });
  await expect(wakeAudioSwitch).toHaveAttribute("aria-checked", "false");
  await page.getByLabel("Model artifact").selectOption("stable");
  await page.getByLabel("Activation detector ID").fill("wn9s_hiesp");
  await wakeAudioSwitch.click();
  await page.getByRole("button", { name: "Tạo wake profile draft" }).click();
  await expect.poll(() => wakeProfileCalls).toEqual([
    expect.objectContaining({
      artifactId: "stable",
      sendWakeAudio: true,
      interrupt: null,
    }),
  ]);
  await page.locator('[data-page-link="devices"]').first().click();
  await page.getByRole("tab", { name: /Wake word/ }).click();
  await page.locator("[data-apply-wake-profile]").click();
  await expect.poll(() => rolloutCalls).toEqual([
    {
      wakeProfileId: "a9dc1d82-e265-47cc-a6a0-73f938dcf3b8",
      deviceIds: [deviceId],
    },
  ]);
  await expect(page.locator(".vt-toast")).toContainText("desired rollout");
});

test("creates and controls a signed firmware canary rollout", async ({ page }) => {
  const firmwareRolloutCalls: unknown[] = [];
  await mockManagerApi(page, {
    withDevice: true,
    withFirmware: true,
    firmwareRolloutCalls,
  });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await page.locator('[data-page-link="resources"]').first().click();
  await page.getByRole("tab", { name: /Firmware OTA/ }).click();

  await expect(page.locator(".firmware-rollout-grid")).toContainText("Firmware A/B");
  await page.getByLabel("Firmware release").selectOption("fw-0.4.0");
  await page.getByLabel("Thiết bị canary").selectOption(deviceId);
  await page.getByLabel("Phần trăm fleet").fill("25");
  await page.getByRole("button", { name: "Bắt đầu rollout" }).click();
  await expect.poll(() => firmwareRolloutCalls).toContainEqual({
    action: "create",
    body: {
      artifactId: "fw-0.4.0",
      percentage: 25,
      canaryDeviceIds: [deviceId],
    },
  });

  await page.getByRole("button", { name: "Mở rộng +10%" }).click();
  await expect.poll(() => firmwareRolloutCalls).toContainEqual({
    action: "resume",
    id: "rollout-1",
    body: { percentage: 20 },
  });
});

test("opens operations deep link with privacy, firmware and redacted audit data", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/#/operations");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await expect(page.locator('[data-page="operations"]')).toBeVisible();
  await expect(page.getByText("Không cần mua domain để vận hành Veetee.")).toBeVisible();
  await expect(page.getByText("device.pair")).toBeVisible();
  await expect(page).toHaveURL(/#\/operations$/);
});

test("filters the device fleet without losing the selected device", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/#/devices");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await expect(page.locator('[data-page="devices"]')).toBeVisible();
  await page.locator(".device-filter-panel select").first().selectOption("offline");
  await expect(page.getByText("Không có thiết bị phù hợp")).toBeVisible();
  await page.getByRole("button", { name: /Xóa bộ lọc/ }).click();
  await expect(page.locator(".device-detail h2")).toHaveText("Veetee Lab");
});

test("keeps device workspaces separated and agent identity fields aligned", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/#/devices");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  const filterBox = await page.locator(".device-filter-panel").boundingBox();
  const deviceLayoutBox = await page.locator(".device-layout").boundingBox();
  expect(filterBox).not.toBeNull();
  expect(deviceLayoutBox).not.toBeNull();
  expect(deviceLayoutBox!.y - (filterBox!.y + filterBox!.height)).toBeGreaterThanOrEqual(16);

  const deliveryHeading = page.locator(".delivery-state-heading");
  const deliveryBadge = deliveryHeading.locator(".vt-badge");
  await expect(deliveryBadge).toHaveCount(1);
  const deliveryHeadingBox = await deliveryHeading.boundingBox();
  const deliveryBadgeBox = await deliveryBadge.boundingBox();
  expect(deliveryHeadingBox).not.toBeNull();
  expect(deliveryBadgeBox).not.toBeNull();
  expect(deliveryBadgeBox!.x).toBeGreaterThanOrEqual(deliveryHeadingBox!.x);
  expect(deliveryBadgeBox!.x + deliveryBadgeBox!.width).toBeLessThanOrEqual(
    deliveryHeadingBox!.x + deliveryHeadingBox!.width + 1,
  );

  await page.getByRole("tab", { name: /Display \/ UI/ }).click();
  await expect.poll(async () => {
    const [capabilityBox, studioBox] = await Promise.all([
      page.locator(".capability-gate").boundingBox(),
      page.locator(".studio-layout").boundingBox(),
    ]);
    if (!capabilityBox || !studioBox) return -1;
    return studioBox.y - (capabilityBox.y + capabilityBox.height);
  }).toBeGreaterThanOrEqual(16);

  await page.getByRole("tab", { name: /Wake word/ }).click();
  await expect.poll(async () => {
    const [capabilityBox, wakeContentBox] = await Promise.all([
      page.locator(".wake-device-panel .capability-gate").boundingBox(),
      page.locator(".wake-device-panel .content-grid").boundingBox(),
    ]);
    if (!capabilityBox || !wakeContentBox) return Number.POSITIVE_INFINITY;
    const gap = wakeContentBox.y - (capabilityBox.y + capabilityBox.height);
    return Math.abs(gap - 18);
  }).toBeLessThanOrEqual(1);

  await page.locator('[data-page-link="agents"]').first().click();
  const identityGrid = page.locator(".form-section").first().locator(".form-grid.two");
  const nameField = page.getByLabel("Tên trợ lý").locator("..");
  const localeField = page.getByLabel("Ngôn ngữ mặc định").locator("..");
  const modeField = page.getByLabel("Chế độ tương tác").locator("..");
  const [identityGridBox, nameFieldBox, localeFieldBox, modeFieldBox] = await Promise.all([
    identityGrid.boundingBox(),
    nameField.boundingBox(),
    localeField.boundingBox(),
    modeField.boundingBox(),
  ]);
  expect(identityGridBox).not.toBeNull();
  expect(nameFieldBox).not.toBeNull();
  expect(localeFieldBox).not.toBeNull();
  expect(modeFieldBox).not.toBeNull();
  expect(Math.abs(nameFieldBox!.y - localeFieldBox!.y)).toBeLessThanOrEqual(1.1);
  expect(Math.abs(nameFieldBox!.height - localeFieldBox!.height)).toBeLessThanOrEqual(1);
  expect(Math.abs(modeFieldBox!.x - identityGridBox!.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(modeFieldBox!.width - identityGridBox!.width)).toBeLessThanOrEqual(1);
  await expect(page.getByLabel("Nguồn múi giờ")).toHaveValue("device");
  await page.getByLabel("Nguồn múi giờ").selectOption("fixed");
  await expect(page.getByLabel("Múi giờ fallback")).toBeVisible();
  await page.getByLabel("Nguồn múi giờ").selectOption("device");
  await expect(page.locator(".personality-grid")).toBeVisible();
  await expect(page.locator(".prompt-render-preview")).toBeVisible();

  await page.locator('[data-page-link="resources"]').first().click();
  await page.getByRole("tab", { name: /Wake profiles/ }).click();
  const wakeFormPanel = page.locator(".resource-tabs .tab-panel:visible .form-section");
  const [wakeHeaderBox, wakeFormBox] = await Promise.all([
    wakeFormPanel.locator(".panel-header > div").boundingBox(),
    wakeFormPanel.locator("form").boundingBox(),
  ]);
  expect(wakeHeaderBox).not.toBeNull();
  expect(wakeFormBox).not.toBeNull();
  expect(Math.abs(wakeHeaderBox!.x - wakeFormBox!.x)).toBeLessThanOrEqual(1);
});

test("supports all direct routes, invalid redirects, forward history and skip focus", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/#/not-a-manager-route");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  await expect(page).toHaveURL(/#\/overview$/);

  for (const route of ["overview", "devices", "agents", "providers", "mcp", "memory", "lab", "resources", "operations"]) {
    await page.goto(`/#/${route}`);
    await expect(page.locator(`[data-page="${route}"]`)).toBeVisible();
    await expect(page.locator("#manager-content")).toBeFocused();
  }

  await page.goto("/#/overview");
  await page.locator('[data-page-link="devices"]').first().click();
  await page.goBack();
  await expect(page).toHaveURL(/#\/overview$/);
  await page.goForward();
  await expect(page).toHaveURL(/#\/devices$/);
  await page.locator(".skip-link").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#manager-content")).toBeFocused();
});

test("keeps representative routes inside every accepted viewport", async ({ page }) => {
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  for (const width of [320, 390, 720, 1024, 1440]) {
    await page.setViewportSize({ width, height: width <= 720 ? 844 : 1000 });
    for (const route of ["overview", "devices", "agents", "mcp", "memory", "resources", "operations"]) {
      await page.goto(`/#/${route}`);
      await expect(page.locator(`[data-page="${route}"]`)).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
    }
  }
});

test("closes the mobile drawer with Escape and removes nonessential reduced-motion transforms", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockManagerApi(page);
  await page.goto("/");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();

  const toggle = page.locator(".mobile-menu-button");
  await toggle.click();
  await page.keyboard.press("Escape");
  await expect(page.locator(".mobile-nav-panel")).toBeHidden();
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".vt-button").first()).toHaveCSS("transition-duration", "1e-05s");
  await page.locator(".vt-button").first().hover();
  await expect(page.locator(".vt-button").first()).toHaveCSS("transform", "none");
});

test("keeps every top-level screen inside the mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem("veetee.manager.theme", "dark"));
  await mockManagerApi(page, { withDevice: true });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByLabel("Email").fill("owner@veetee.local");
  await page.getByLabel("Mật khẩu").fill("test-password");
  await page.getByRole("button", { name: /Vào trang vận hành/ }).click();
  const screens = [
    ["Tổng quan", "overview"], ["Thiết bị", "devices"], ["Trợ lý", "agents"],
    ["Nhà cung cấp AI", "providers"], ["Remote MCP", "mcp"], ["Bộ nhớ", "memory"], ["Realtime Lab", "lab"],
    ["Tài nguyên", "resources"], ["Vận hành", "operations"],
  ] as const;
  for (const [index, [screen, route]] of screens.entries()) {
    if (index > 0) {
      await page.locator(".mobile-menu-button").click();
      await page.locator(`.mobile-nav-panel [data-page-link="${route}"]`).click();
    }
    await expect(page.locator("main .vt-page")).toBeVisible();
    const width = await page.evaluate(() => ({ innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    expect(width.scrollWidth, `${screen} overflows mobile viewport`).toBeLessThanOrEqual(width.innerWidth + 1);
  }
});
