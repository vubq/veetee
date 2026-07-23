import { Algorithm, hash } from "@node-rs/argon2";
import { TenantRole } from "@prisma/client";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { AuditService } from "../audit/audit.service.js";
import { ArtifactFilesService } from "../artifacts/artifact-files.service.js";
import { ResourceCatalogService } from "../artifacts/resource-catalog.service.js";
import { ResourceManifestService } from "../artifacts/resource-manifest.service.js";
import { AuthService } from "../auth/auth.service.js";
import type { Principal } from "../auth/auth.types.js";
import { DEFAULT_AGENT_BASE_PROMPT } from "../config/agent-prompt.policy.js";
import { PrismaService } from "../database/prisma.service.js";
import { RedisService } from "../database/redis.service.js";
import { PairingService } from "../pairing/pairing.service.js";
import { SecretCryptoService } from "../security/secret-crypto.service.js";
import { ControlPlaneStore } from "./control-plane.store.js";

if (process.env.VEETEE_INTEGRATION === "1") {
  const databaseUrl = process.env.DATABASE_URL;
  let databaseName = "";
  try {
    databaseName = databaseUrl ? new URL(databaseUrl).pathname.replace(/^\//, "") : "";
  } catch {
    databaseName = "";
  }
  if (!databaseName.endsWith("_test")) {
    throw new Error(
      "Integration tests require DATABASE_URL to point to a dedicated *_test database",
    );
  }
}

const reportedCapabilities = {
  capabilities: {
    board: "veetee-s3-n16r8",
    display: {
      target: "st7789-240x280-rgb565", controller: "st7789", width: 240, height: 280,
      colorFormat: "rgb565", resourceAbi: 2, uiAbi: 1, slotBytes: 2_097_152,
      hotReload: true, compositions: ["signal", "monolith", "quiet"],
    },
    wake: {
      runtime: "esp-sr", runtimeAbi: 1, resourceAbi: 1, slotBytes: 2_097_152,
      sampleRateHz: 16_000, channels: 1, hotReload: true,
    },
  },
};

describe.runIf(process.env.VEETEE_INTEGRATION === "1")("persistent ControlPlaneStore", () => {
  const prisma = new PrismaService();
  const redis = new RedisService();
  const audit = new AuditService(prisma);
  const auth = new AuthService(prisma);
  const store = new ControlPlaneStore(
    prisma,
    redis,
    new PairingService(redis),
    audit,
    new SecretCryptoService(),
  );
  const resourceCatalog = new ResourceCatalogService(
    prisma,
    audit,
    new ResourceManifestService(new ArtifactFilesService()),
  );
  let principal: Principal;

  beforeAll(async () => {
    process.env.VEETEE_MASTER_KEY = Buffer.alloc(32, 7).toString("base64");
    process.env.VEETEE_AUTH_SECRET = "integration-auth-secret-with-at-least-32-characters";
    await prisma.$connect();
    await redis.client.connect();
    await redis.client.flushdb();
    await prisma.auditEvent.deleteMany();
    await prisma.conversationEvent.deleteMany();
    await prisma.resourceRollout.deleteMany();
    await prisma.wakeProfileVersion.deleteMany();
    await prisma.wakeProfile.deleteMany();
    await prisma.artifact.deleteMany();
    await prisma.refreshSession.deleteMany();
    await prisma.deviceReportedState.deleteMany();
    await prisma.deviceDesiredState.deleteMany();
    await prisma.device.deleteMany();
    await prisma.agentConfigVersion.deleteMany();
    await prisma.agent.deleteMany();
    await prisma.personalityPreset.deleteMany();
    await prisma.providerBinding.deleteMany();
    await prisma.membership.deleteMany();
    await prisma.user.deleteMany();
    await prisma.tenant.deleteMany();
    const tenant = await prisma.tenant.create({ data: { slug: "integration", name: "Integration" } });
    const user = await prisma.user.create({
      data: {
        email: "integration@veetee.local",
        displayName: "Integration Owner",
        passwordHash: await hash("integration-password", {
          algorithm: Algorithm.Argon2id,
          memoryCost: 4_096,
          timeCost: 1,
        }),
      },
    });
    await prisma.membership.create({
      data: { tenantId: tenant.id, userId: user.id, role: TenantRole.OWNER },
    });
    principal = {
      userId: user.id,
      tenantId: tenant.id,
      tenantSlug: tenant.slug,
      role: TenantRole.OWNER,
      email: user.email,
      displayName: user.displayName,
    };
  });

  afterAll(async () => {
    await redis.client.quit();
    await prisma.$disconnect();
  });

  it("persists pairing, device activation and monotonic desired/reported state", async () => {
    const providerIds: Record<string, string> = {};
    for (const kind of ["vad", "asr", "llm", "tts"] as const) {
      const provider = await store.createProvider(
        {
          kind,
          adapter: `${kind}-integration`,
          model: `${kind}-model`,
          ...(kind === "llm" ? { secret: "integration-provider-secret" } : {}),
          enabled: true,
          priority: 10,
          locales: ["vi-VN"],
        },
        { principal, requestId: `integration-provider-${kind}` },
      );
      providerIds[kind] = provider.id;
      expect(JSON.stringify(provider)).not.toContain("integration-provider-secret");
    }
    const llmId = providerIds.llm as string;
    const rotated = await store.updateProvider(
      llmId,
      { secretAction: "rotate", secret: "rotated-provider-secret" },
      { principal, requestId: "integration-provider-rotate" },
    );
    expect(rotated.secretConfigured).toBe(true);
    expect(JSON.stringify(rotated)).not.toContain("rotated-provider-secret");
    await expect(store.resolveProviderRuntime([llmId])).resolves.toEqual([
      expect.objectContaining({ id: llmId, secret: "rotated-provider-secret" }),
    ]);
    const cleared = await store.updateProvider(
      llmId,
      { secretAction: "clear" },
      { principal, requestId: "integration-provider-clear" },
    );
    expect(cleared.secretConfigured).toBe(false);
    await expect(store.resolveProviderRuntime([llmId])).resolves.toEqual([
      expect.not.objectContaining({ secret: expect.anything() }),
    ]);

    const agent = await store.createAgent(
      {
        name: "Integration Agent",
        defaultLocale: "vi-VN",
        interactionMode: "auto",
        draftConfig: {
          prompt: {
            schemaVersion: 1,
            template: "You are {{agent_name}}. Reply in {{language}}.",
            language: "Tiếng Việt",
            timeZone: "",
            timeZoneSource: "device",
            personalityPresetId: "",
            customPersonality: "",
            responseStyle: "",
            userAddress: "",
          },
          providerChains: ["vad", "asr", "llm", "tts"].map((kind) => ({
            kind,
            locale: "vi-VN",
            providerIds: [providerIds[kind]],
          })),
        },
      },
      { principal, requestId: "integration-agent" },
    );
    expect(agent.persona).toBe("");
    const published = await store.publishAgent(agent.id, {
      principal,
      requestId: "integration-publish",
    });
    const edited = await store.updateAgent(
      agent.id,
      { persona: "Updated Vietnamese integration agent" },
      { principal, requestId: "integration-update" },
    );
    expect(edited.persona).toBe("Updated Vietnamese integration agent");
    const ticket = await store.createPairingCode("esp32-integration");
    const device = await store.claimPairing(
      ticket.code,
      "Veetee Integration",
      { principal, requestId: "integration-pair" },
      agent.id,
    );
    const unassigned = await store.assignDeviceAgent(
      device.id,
      undefined,
      { principal, requestId: "integration-agent-unassign" },
    );
    expect(unassigned.agentId).toBeUndefined();
    expect(unassigned.desiredState.state).not.toHaveProperty("agentConfigVersion");
    const assigned = await store.assignDeviceAgent(
      device.id,
      agent.id,
      { principal, requestId: "integration-agent-assign" },
    );
    expect(assigned).toMatchObject({
      agentId: agent.id,
      desiredState: {
        version: 3,
        state: { agentId: agent.id, agentConfigVersion: published.publishedVersion },
      },
    });
    await expect(
      store.claimPairing(ticket.code, "Duplicate", { principal, requestId: "duplicate" }),
    ).rejects.toThrow();
    const activation = await store.activateDevice("esp32-integration", ticket.challenge);
    expect(typeof activation?.token).toBe("string");
    await expect(store.activateDevice("esp32-integration", ticket.challenge)).resolves.toEqual(
      activation,
    );
    await expect(
      store.authenticateDevice(device.id, String(activation?.token)),
    ).resolves.toMatchObject({ id: device.id });
    const republished = await store.publishAgent(agent.id, {
      principal,
      requestId: "integration-republish",
    });
    expect(republished.publishedVersion).toBe(published.publishedVersion + 1);
    await expect(
      store.authenticateDeviceByHardware("esp32-integration", String(activation?.token)),
    ).resolves.toMatchObject({ configVersion: published.publishedVersion });
    const rolledAgent = await store.assignDeviceAgent(
      device.id,
      agent.id,
      { principal, requestId: "integration-agent-rollout" },
    );
    expect(rolledAgent.desiredState.version).toBe(4);
    await expect(
      store.authenticateDeviceByHardware("esp32-integration", String(activation?.token)),
    ).resolves.toMatchObject({ configVersion: republished.publishedVersion });
    const desired = await store.setDesiredState(
      device.id,
      { agentConfigVersion: published.publishedVersion, resourceBundleVersion: "1.0.0" },
      { principal, requestId: "integration-desired" },
    );
    expect(desired.desiredState.version).toBe(5);
    const reported = await store.updateReportedState(
      device.id,
      2,
      { agentConfigVersion: published.publishedVersion, resourceBundleVersion: "0.9.0" },
      "boot-integration",
    );
    expect(reported.reportedState.state).not.toEqual(reported.desiredState.state);
    const idempotent = await store.updateReportedState(device.id, 2, { unexpected: true });
    expect(idempotent.reportedState.state).toEqual(reported.reportedState.state);
    await Promise.allSettled([
      store.updateReportedState(device.id, 4, { marker: "newest", ...reportedCapabilities }),
      store.updateReportedState(device.id, 3, { marker: "older" }),
    ]);
    const concurrent = await store.updateReportedState(device.id, 4, { unexpected: true });
    expect(concurrent.reportedState).toMatchObject({
      version: 4,
      state: { marker: "newest", ...reportedCapabilities },
    });
    await expect(store.updateReportedState(device.id, 1, {})).rejects.toThrow(/stale/i);
    await expect(store.getAgentConfig(agent.id, published.publishedVersion)).resolves.toMatchObject({
      agentId: agent.id,
      agentName: "Integration Agent",
      interactionMode: "auto",
      prompt: {
        schemaVersion: 1,
        personalityPresetId: "",
        personality: "",
        language: "Tiếng Việt",
      },
    });

    const eventId = "98bdb294-4dd1-42ce-87fa-79f414c22c59";
    const conversationEvent = {
      eventId,
      sessionId: "session_integration_01",
      turnId: "session_integration_01:1",
      generation: 2,
      eventType: "admission",
      payload: { disposition: "accepted", confidence: 0.94 },
      occurredAt: "2026-07-22T04:15:00.000Z",
    };
    await expect(
      store.ingestConversationEvents(device.id, [conversationEvent]),
    ).resolves.toEqual({ accepted: 1 });
    await expect(
      store.ingestConversationEvents(device.id, [conversationEvent]),
    ).resolves.toEqual({ accepted: 0 });
    await expect(store.listConversationEvents(principal.tenantId, device.id, 10)).resolves.toEqual([
      expect.objectContaining({
        id: eventId,
        deviceId: device.id,
        agentId: agent.id,
        sessionId: conversationEvent.sessionId,
        eventType: "admission",
      }),
    ]);

    await prisma.conversationEvent.update({
      where: { id: eventId },
      data: { retentionUntil: new Date(Date.now() - 1_000) },
    });
    await expect(store.listConversationEvents(principal.tenantId, device.id, 10)).resolves.toEqual(
      [],
    );
    await store.ingestConversationEvents(device.id, [
      {
        ...conversationEvent,
        eventId: "57a85bd1-b0cb-4353-982d-185001579021",
        eventType: "assistant.sleep",
      },
    ]);
    await expect(prisma.conversationEvent.findUnique({ where: { id: eventId } })).resolves.toBeNull();

    await store.bootstrapDevice("esp32-integration", activation?.token, "0.2.0");
    const artifact = await resourceCatalog.registerArtifact(
      "stable",
      "ESP-SR model pack bring-up; benchmark not yet a Hey VeeTee product pass",
      "not_run",
      { principal, requestId: "integration-artifact-register" },
    );
    expect(artifact).toMatchObject({
      status: "validated",
      runtime: "esp-sr",
      benchmarkStatus: "not_run",
    });
    await resourceCatalog.publishArtifact(artifact.id, {
      principal,
      requestId: "integration-artifact-publish",
    });
    const wakeProfile = await resourceCatalog.createWakeProfile(
      {
        artifactId: artifact.id,
        name: "ESP-SR bring-up",
        locale: "vi-VN",
        channel: "development",
        activationPhrase: "Hi ESP",
        activation: {
          detectorId: "wakenet:hi_esp",
          sensitivity: 0.5,
          cooldownMs: 1_500,
          allowedStates: ["standby"],
        },
        interrupt: {
          detectorId: "multinet:stop",
          sensitivity: 0.6,
          cooldownMs: 800,
          allowedStates: ["thinking", "speaking"],
        },
      },
      { principal, requestId: "integration-wake-create" },
    );
    expect(wakeProfile.productReady).toBe(false);
    const publishedWake = await resourceCatalog.publishWakeProfile(wakeProfile.id, {
      principal,
      requestId: "integration-wake-publish",
    });
    const rollouts = await resourceCatalog.rollout(
      wakeProfile.id,
      publishedWake.publishedVersion,
      [device.id],
      { principal, requestId: "integration-resource-rollout" },
    );
    expect(rollouts).toHaveLength(1);
    await expect(store.device(principal.tenantId, device.id)).resolves.toMatchObject({
      desiredState: {
        state: {
          resourceBundleVersion: "1.0.0",
          resourceManifestId: "stable",
          wakeProfile: {
            activationPhrase: "Hi ESP",
            productReady: false,
          },
        },
      },
    });
    await store.updateReportedState(
      device.id,
      5,
      {
        schemaVersion: 1,
        firmware: { version: "0.2.0" },
        resource: {
          phase: "active",
          currentVersion: "1.0.0",
          desiredVersion: "1.0.0",
          activeSlot: 1,
          targetSlot: 1,
          expectedBytes: 125_943,
          downloadedBytes: 125_943,
          securityEpoch: 1,
        },
      },
      "d83018a5-b419-48cc-af33-7fd0d753f389",
    );
    await expect(resourceCatalog.listRollouts(principal.tenantId)).resolves.toEqual([
      expect.objectContaining({ id: rollouts[0]?.id, status: "complete" }),
    ]);
  });

  it("atomically rotates a refresh token", async () => {
    const pair = await auth.login("integration@veetee.local", "integration-password");
    const attempts = await Promise.allSettled([
      auth.refresh(pair.refreshToken),
      auth.refresh(pair.refreshToken),
    ]);
    expect(attempts.filter((attempt) => attempt.status === "fulfilled")).toHaveLength(1);
    expect(attempts.filter((attempt) => attempt.status === "rejected")).toHaveLength(1);
  });

  it("persists, publishes and safely deletes tenant personality presets", async () => {
    const preset = await store.createPersonalityPreset(
      {
        label: "Cà khịa vui",
        summary: "Trêu nhẹ, sắc nhưng biết dừng đúng lúc.",
        accent: "coral",
        instructions: "Trêu nhẹ theo ngữ cảnh và phản biện lập luận thay vì công kích.",
      },
      { principal, requestId: "integration-personality-create" },
    );
    await expect(store.getAgentPromptCatalog(principal.tenantId)).resolves.toEqual(
      expect.objectContaining({
        personalityPresets: expect.arrayContaining([
          expect.objectContaining({ id: preset.id, builtIn: false, deletable: true }),
        ]),
      }),
    );
    const [agent] = await store.listAgents(principal.tenantId);
    if (!agent) throw new Error("Integration agent is missing");
    const customPrompt = {
      schemaVersion: 1,
      template: DEFAULT_AGENT_BASE_PROMPT,
      language: "Tiếng Việt tự nhiên",
      timeZone: "Asia/Bangkok",
      timeZoneSource: "device",
      personalityPresetId: preset.id,
      customPersonality: "",
      responseStyle: "Ngắn và rõ.",
      userAddress: "bạn",
    };
    await store.updateAgent(
      agent.id,
      { draftConfig: { ...agent.draftConfig, prompt: customPrompt } },
      { principal, requestId: "integration-personality-select" },
    );
    const published = await store.publishAgent(agent.id, {
      principal,
      requestId: "integration-personality-publish",
    });
    await expect(store.getAgentConfig(agent.id, published.publishedVersion)).resolves.toMatchObject({
      prompt: {
        personalityPresetId: preset.id,
        personalityLabel: preset.label,
        personality: expect.stringContaining("phản biện lập luận"),
      },
    });
    await expect(
      store.deletePersonalityPreset(preset.id, {
        principal,
        requestId: "integration-personality-delete-in-use",
      }),
    ).rejects.toThrow(/used by agent draft/i);
    await store.updateAgent(
      agent.id,
      {
        draftConfig: {
          ...agent.draftConfig,
          prompt: { ...customPrompt, personalityPresetId: "warm-empathetic" },
        },
      },
      { principal, requestId: "integration-personality-unselect" },
    );
    await expect(
      store.deletePersonalityPreset(preset.id, {
        principal,
        requestId: "integration-personality-delete",
      }),
    ).resolves.toMatchObject({ id: preset.id, deletable: true });
    const catalog = await store.getAgentPromptCatalog(principal.tenantId);
    expect(catalog.personalityPresets.some(({ id }) => id === preset.id)).toBe(false);
    await expect(
      store.deletePersonalityPreset("warm-empathetic", {
        principal,
        requestId: "integration-personality-delete-built-in",
      }),
    ).rejects.toThrow(/built-in/i);
  });
});
