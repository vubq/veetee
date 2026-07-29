import { Algorithm, hash } from "@node-rs/argon2";
import {
  ArtifactKind,
  ArtifactStatus,
  MemoryMessageRole,
  Prisma,
  RemoteMcpCallActor,
  RemoteMcpCallStatus,
  TenantRole,
} from "@prisma/client";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { AuditService } from "../audit/audit.service.js";
import { ResourceCatalogService } from "../artifacts/resource-catalog.service.js";
import { ResourceManifestService } from "../artifacts/resource-manifest.service.js";
import { AuthService } from "../auth/auth.service.js";
import type { Principal } from "../auth/auth.types.js";
import { DEFAULT_AGENT_BASE_PROMPT } from "../config/agent-prompt.policy.js";
import { PrismaService } from "../database/prisma.service.js";
import { RedisService } from "../database/redis.service.js";
import { RemoteMcpService } from "../mcp/remote-mcp.service.js";
import { MemoryService } from "../memory/memory.service.js";
import { FirmwareRolloutService } from "../ota/firmware-rollout.service.js";
import { PairingService } from "../pairing/pairing.service.js";
import { SecretCryptoService } from "../security/secret-crypto.service.js";
import { ControlPlaneStore } from "./control-plane.store.js";
import { mergeDeviceDesiredState } from "./device-desired-state.js";

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

const integrationResourceManifest = {
  manifest_version: 1,
  bundle_id: "integration-resource",
  kind: "resource_bundle",
  version: "1.0.0",
  channel: "development",
  members: [{
    name: "speech/esp-sr-models",
    kind: "model_pack",
    runtime: "esp-sr",
    runtime_abi: 1,
    format_version: 1,
    sample_rate: 16_000,
    detectors: [{ id: "wn9s_hiesp", role: "activation" }],
    sha256: "56fc71dda4bf4ebe6ed87359e3bda7eebef38dc0b8b01ce1203d2cd1dc212562",
    bytes: 125_943,
  }],
};

const integrationManifestService = {
  async validate(artifactId: string) {
    return {
      artifactId,
      kind: "resource_bundle" as const,
      version: "1.0.0",
      channel: "development",
      sizeBytes: 125_943,
      sha256: "56fc71dda4bf4ebe6ed87359e3bda7eebef38dc0b8b01ce1203d2cd1dc212562",
      contentType: "application/vnd.veetee.esp-sr-model-pack",
      runtime: "esp-sr",
      runtimeAbi: 1,
      board: "veetee-s3-n16r8",
      minFirmware: "0.2.0",
      maxFirmware: "0.3.0",
      signatureKeyId: "integration-release-key",
      securityEpoch: 1,
      detectors: [{ id: "wn9s_hiesp", role: "activation" as const }],
      manifest: structuredClone(integrationResourceManifest),
    };
  },
} as unknown as ResourceManifestService;

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
    integrationManifestService,
  );
  const firmwareRollouts = new FirmwareRolloutService(prisma, audit);
  const memory = new MemoryService(prisma, audit);
  const remoteMcp = new RemoteMcpService(prisma, audit, new SecretCryptoService());
  let principal: Principal;

  beforeAll(async () => {
    process.env.VEETEE_MASTER_KEY = Buffer.alloc(32, 7).toString("base64");
    process.env.VEETEE_AUTH_SECRET = "integration-auth-secret-with-at-least-32-characters";
    await prisma.$connect();
    await redis.client.connect();
    await redis.client.flushdb();
    await prisma.auditEvent.deleteMany();
    await prisma.remoteMcpInvocation.deleteMany();
    await prisma.agentRemoteMcpAssignment.deleteMany();
    await prisma.remoteMcpEndpoint.deleteMany();
    await prisma.conversationMemoryFact.deleteMany();
    await prisma.conversationMemoryMessage.deleteMany();
    await prisma.memoryWriteReceipt.deleteMany();
    await prisma.conversationEvent.deleteMany();
    await prisma.firmwareRollout.deleteMany();
    await prisma.uiPackRollout.deleteMany();
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
    expect(activation?.configVersion).toBe(3);
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
    const configMerged = await store.updateReportedState(device.id, 3, {
      schemaVersion: 1,
      firmware: { version: "0.2.0" },
      config: { desiredVersion: 5, appliedVersion: 4, phase: "applying" },
    });
    expect(configMerged.reportedState.state).toMatchObject({
      resourceBundleVersion: "0.9.0",
      config: { desiredVersion: 5, appliedVersion: 4, phase: "applying" },
    });
    const artifactMerged = await store.updateReportedState(device.id, 4, {
      schemaVersion: 1,
      firmware: { version: "0.2.0" },
      resource: { phase: "checking" },
      // class-transformer DTO instances define absent optional fields as
      // own properties with undefined values. They must not erase a
      // previously reported subsystem during the shallow merge.
      config: undefined,
    });
    expect(artifactMerged.reportedState.state).toMatchObject({
      config: { desiredVersion: 5, appliedVersion: 4, phase: "applying" },
      resource: { phase: "checking" },
    });
    const equalMerged = await store.updateReportedState(device.id, 4, { unexpected: true });
    expect(equalMerged.reportedState.state).toEqual(artifactMerged.reportedState.state);
    await Promise.allSettled([
      store.updateReportedState(device.id, 6, { marker: "newest", ...reportedCapabilities }),
      store.updateReportedState(device.id, 5, { marker: "older" }),
    ]);
    const concurrent = await store.updateReportedState(device.id, 6, { unexpected: true });
    expect(concurrent.reportedState).toMatchObject({
      version: 6,
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

    await expect(
      store.bootstrapDevice("esp32-integration", activation?.token, "0.2.0"),
    ).resolves.toMatchObject({ configVersion: 5 });
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
    await expect(resourceCatalog.createWakeProfile(
      {
        artifactId: artifact.id,
        name: "Undeclared detector",
        locale: "vi-VN",
        channel: "development",
        activationPhrase: "Not in the model pack",
        activation: {
          detectorId: "wn_fake",
          sensitivity: 0.5,
          cooldownMs: 1_500,
          allowedStates: ["standby"],
        },
        interrupt: null,
      },
      { principal, requestId: "integration-wake-undeclared" },
    )).rejects.toThrow(/not declared by the selected signed model pack/);
    await expect(resourceCatalog.createWakeProfile(
      {
        artifactId: artifact.id,
        name: "Invalid shared model",
        locale: "vi-VN",
        channel: "development",
        activationPhrase: "Hi ESP",
        activation: {
          detectorId: "wn9s_hiesp",
          sensitivity: 0.5,
          cooldownMs: 1_500,
          allowedStates: ["standby"],
        },
        interrupt: {
          detectorId: "wn9s_hiesp",
          sensitivity: 0.7,
          cooldownMs: 750,
          allowedStates: ["thinking", "speaking"],
        },
      },
      { principal, requestId: "integration-wake-conflict" },
    )).rejects.toThrow(/different WakeNet models/);
    await expect(prisma.wakeProfile.count({
      where: { tenantId: principal.tenantId, name: "Invalid shared model" },
    })).resolves.toBe(0);
    const wakeProfile = await resourceCatalog.createWakeProfile(
      {
        artifactId: artifact.id,
        name: "ESP-SR bring-up",
        locale: "vi-VN",
        channel: "development",
        activationPhrase: "Hi ESP",
        sendWakeAudio: true,
        activation: {
          detectorId: "wn9s_hiesp",
          sensitivity: 0.5,
          cooldownMs: 1_500,
          allowedStates: ["standby"],
        },
        interrupt: null,
      },
      { principal, requestId: "integration-wake-create" },
    );
    expect(wakeProfile.productReady).toBe(false);
    const publishedWake = await resourceCatalog.publishWakeProfile(wakeProfile.id, {
      principal,
      requestId: "integration-wake-publish",
    });
    const publishedVersion = await prisma.wakeProfileVersion.findUniqueOrThrow({
      where: {
        wakeProfileId_version: {
          wakeProfileId: wakeProfile.id,
          version: publishedWake.publishedVersion,
        },
      },
    });
    const validSnapshot = publishedVersion.snapshot as Prisma.JsonObject;
    await prisma.wakeProfileVersion.update({
      where: { id: publishedVersion.id },
      data: {
        snapshot: {
          ...validSnapshot,
          interrupt: {
            ...(validSnapshot.activation as Prisma.JsonObject),
            allowedStates: ["thinking", "speaking"],
          },
        },
      },
    });
    await expect(resourceCatalog.rollout(
      wakeProfile.id,
      publishedWake.publishedVersion,
      [device.id],
      { principal, requestId: "integration-invalid-history-rollout" },
    )).rejects.toThrow(/different WakeNet models/);
    await prisma.wakeProfileVersion.update({
      where: { id: publishedVersion.id },
      data: { snapshot: validSnapshot },
    });
    const originalArtifact = await prisma.artifact.findUniqueOrThrow({
      where: { id: artifact.id },
    });
    const nextArtifactId = "stable-next";
    await prisma.artifact.create({
      data: {
        id: nextArtifactId,
        tenantId: principal.tenantId,
        kind: originalArtifact.kind,
        version: "2.0.0",
        channel: originalArtifact.channel,
        sizeBytes: originalArtifact.sizeBytes,
        sha256: originalArtifact.sha256,
        contentType: originalArtifact.contentType,
        runtime: originalArtifact.runtime,
        runtimeAbi: originalArtifact.runtimeAbi,
        license: originalArtifact.license,
        board: originalArtifact.board,
        minFirmware: originalArtifact.minFirmware,
        maxFirmware: originalArtifact.maxFirmware,
        signatureKeyId: originalArtifact.signatureKeyId,
        securityEpoch: originalArtifact.securityEpoch,
        benchmarkStatus: originalArtifact.benchmarkStatus,
        status: originalArtifact.status,
        manifest: originalArtifact.manifest as Prisma.InputJsonValue,
        publishedAt: new Date(),
      },
    });
    await resourceCatalog.updateWakeProfile(
      wakeProfile.id,
      {
        artifactId: nextArtifactId,
        name: "ESP-SR bring-up",
        locale: "vi-VN",
        channel: "development",
        activationPhrase: "Hi ESP",
        activation: {
          detectorId: "wn9s_hiesp",
          sensitivity: 0.5,
          cooldownMs: 1_500,
          allowedStates: ["standby"],
        },
        interrupt: null,
      },
      { principal, requestId: "integration-wake-next-artifact" },
    );
    const rollouts = await resourceCatalog.rollout(
      wakeProfile.id,
      publishedWake.publishedVersion,
      [device.id],
      { principal, requestId: "integration-resource-rollout" },
    );
    expect(rollouts).toHaveLength(1);
    expect(rollouts[0]).toMatchObject({ artifactId: artifact.id });
    await expect(store.device(principal.tenantId, device.id)).resolves.toMatchObject({
      desiredState: {
        state: {
          resourceBundleVersion: "1.0.0",
          resourceManifestId: "stable",
          wakeProfile: {
            artifactId: "stable",
            activationPhrase: "Hi ESP",
            sendWakeAudio: true,
            productReady: false,
          },
        },
      },
    });
    await store.updateReportedState(
      device.id,
      7,
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

  it("serializes concurrent resource, UI and firmware desired-state merges", async () => {
    const device = await prisma.device.findUniqueOrThrow({
      where: { hardwareId: "esp32-integration" },
      include: { desiredState: true },
    });
    const baselineVersion = device.desiredState?.version ?? 0;
    const patches = [
      {
        resourceBundleVersion: "race-resource",
        resourceManifestId: "race-resource-manifest",
      },
      {
        uiPackVersion: "race-ui",
        uiManifestId: "race-ui-manifest",
      },
      {
        firmwareVersion: "race-firmware",
        firmwareManifestId: "race-firmware-manifest",
      },
    ];

    const mutations = await Promise.all(patches.map((patch) =>
      prisma.$transaction((transaction) =>
        mergeDeviceDesiredState(
          transaction,
          device.id,
          principal.tenantId,
          patch,
        ))));

    expect(mutations.every((mutation) => mutation !== null)).toBe(true);
    expect(mutations
      .map((mutation) => mutation?.version)
      .sort((left, right) => Number(left) - Number(right))).toEqual([
      baselineVersion + 1,
      baselineVersion + 2,
      baselineVersion + 3,
    ]);
    const desired = await prisma.deviceDesiredState.findUniqueOrThrow({
      where: { deviceId: device.id },
    });
    expect(desired.version).toBe(baselineVersion + 3);
    expect(desired.state).toMatchObject({
      ...(device.desiredState?.state as Prisma.JsonObject),
      ...patches[0],
      ...patches[1],
      ...patches[2],
    });
  });

  it("keeps resource, UI and firmware service mutations under the shared device lock", async () => {
    const device = await prisma.device.findUniqueOrThrow({
      where: { hardwareId: "esp32-integration" },
      include: { desiredState: true },
    });
    const profile = await prisma.wakeProfile.findFirstOrThrow({
      where: { tenantId: principal.tenantId, name: "ESP-SR bring-up" },
    });
    const uiArtifact = await prisma.artifact.create({
      data: {
        id: "race-ui-artifact",
        tenantId: principal.tenantId,
        kind: ArtifactKind.DISPLAY_ASSETS,
        version: "1.1.0",
        channel: "development",
        sizeBytes: 1_024,
        sha256: "a".repeat(64),
        contentType: "application/vnd.veetee.ui-pack",
        runtime: "veetee-ui",
        runtimeAbi: 1,
        license: "integration-only",
        board: "veetee-s3-n16r8",
        minFirmware: "0.2.0",
        maxFirmware: "0.4.0",
        signatureKeyId: "integration-release",
        securityEpoch: 1,
        status: ArtifactStatus.PUBLISHED,
        manifest: { schema_version: 1 },
        publishedAt: new Date(),
      },
    });
    const firmwareArtifact = await prisma.artifact.create({
      data: {
        id: "race-firmware-artifact",
        tenantId: principal.tenantId,
        kind: ArtifactKind.FIRMWARE,
        version: "0.4.0",
        channel: "development",
        sizeBytes: 1_048_576,
        sha256: "b".repeat(64),
        contentType: "application/vnd.veetee.esp32s3-firmware",
        runtime: "esp-idf-image",
        runtimeAbi: 1,
        license: "integration-only",
        board: "veetee-s3-n16r8",
        minFirmware: "0.2.0",
        maxFirmware: "0.5.0",
        signatureKeyId: "integration-release",
        securityEpoch: 1,
        status: ArtifactStatus.PUBLISHED,
        manifest: { schema_version: 1 },
        publishedAt: new Date(),
      },
    });
    const baselineVersion = device.desiredState?.version ?? 0;
    const baselineState = device.desiredState?.state as Prisma.JsonObject;

    const [resource, ui, firmware] = await Promise.all([
      resourceCatalog.rollout(
        profile.id,
        profile.publishedVersion,
        [device.id],
        { principal, requestId: "integration-race-resource" },
      ),
      resourceCatalog.rolloutUiPack(
        uiArtifact.id,
        [device.id],
        { principal, requestId: "integration-race-ui" },
      ),
      firmwareRollouts.createRollout(
        {
          artifactId: firmwareArtifact.id,
          percentage: 0,
          canaryDeviceIds: [device.id],
        },
        { principal, requestId: "integration-race-firmware" },
      ),
    ]);

    expect(resource).toHaveLength(1);
    expect(ui).toHaveLength(1);
    expect(resource[0]?.desiredStateVersion).not.toBe(ui[0]?.desiredStateVersion);
    expect(firmware.selectedDeviceIds).toContain(device.id);
    const desired = await prisma.deviceDesiredState.findUniqueOrThrow({
      where: { deviceId: device.id },
    });
    expect(desired.version).toBe(baselineVersion + 3);
    expect(desired.state).toMatchObject({
      ...baselineState,
      resourceBundleVersion: "1.0.0",
      resourceManifestId: "stable",
      wakeProfile: expect.any(Object),
      uiPackVersion: uiArtifact.version,
      uiManifestId: uiArtifact.id,
      firmwareVersion: firmwareArtifact.version,
      firmwareManifestId: firmwareArtifact.id,
      firmwareChannel: firmwareArtifact.channel,
    });
  });

  it("persists bounded cross-session memory and immutable Remote MCP assignments", async () => {
    const [agent] = await store.listAgents(principal.tenantId);
    const [device] = await store.listDevices(principal.tenantId);
    if (!agent || !device) throw new Error("Integration agent/device is missing");
    const updated = await store.updateAgent(
      agent.id,
      {
        draftConfig: {
          ...agent.draftConfig,
          memoryPolicy: {
            enabled: true,
            consent: true,
            storeMessages: true,
            storeFacts: true,
            retentionDays: 7,
            maxMessages: 2,
            maxMessageCharacters: 2_000,
            maxContextCharacters: 8_000,
            factRetentionDays: 90,
            maxFacts: 50,
            maxFactCharacters: 1_000,
          },
        },
      },
      { principal, requestId: "integration-memory-policy" },
    );
    const endpoint = await remoteMcp.createEndpoint(
      {
        name: "Integration Weather",
        url: "https://93.184.216.34/mcp",
        transport: "streamable_http",
        authType: "bearer",
        secret: "integration-remote-mcp-secret",
        timeoutSeconds: 10,
        resultMaxBytes: 16_384,
        networkPolicy: "public_only",
        allowedHosts: ["93.184.216.34"],
        tools: [{
          name: "weather.current",
          safetyClass: "read_only",
          requiresConfirmation: false,
        }],
      },
      { principal, requestId: "integration-remote-mcp-create" },
    );
    expect(JSON.stringify(endpoint)).not.toContain("integration-remote-mcp-secret");
    const calendarEndpoint = await remoteMcp.createEndpoint(
      {
        name: "Integration Calendar",
        url: "https://93.184.216.35/mcp",
        transport: "streamable_http",
        authType: "none",
        timeoutSeconds: 10,
        resultMaxBytes: 16_384,
        networkPolicy: "public_only",
        allowedHosts: ["93.184.216.35"],
        tools: [{
          name: "calendar.next",
          safetyClass: "read_only",
          requiresConfirmation: false,
        }],
      },
      { principal, requestId: "integration-remote-mcp-calendar-create" },
    );
    await Promise.all([
      remoteMcp.replaceAssignments(
        agent.id,
        [{ endpointId: endpoint.id, toolNames: ["weather.current"], timeoutSeconds: 8 }],
        { principal, requestId: "integration-remote-mcp-race-weather" },
      ),
      remoteMcp.replaceAssignments(
        agent.id,
        [{ endpointId: calendarEndpoint.id, toolNames: ["calendar.next"], timeoutSeconds: 8 }],
        { principal, requestId: "integration-remote-mcp-race-calendar" },
      ),
    ]);
    const racedAssignments = await remoteMcp.listAssignments(principal.tenantId, agent.id);
    expect(racedAssignments.items).toHaveLength(1);
    expect([endpoint.id, calendarEndpoint.id]).toContain(racedAssignments.items[0]?.endpointId);
    await remoteMcp.replaceAssignments(
      agent.id,
      [{ endpointId: endpoint.id, toolNames: ["weather.current"], timeoutSeconds: 8 }],
      { principal, requestId: "integration-remote-mcp-assign" },
    );
    const credentialRaceEndpoint = await remoteMcp.createEndpoint(
      {
        name: "Integration Credential Race",
        url: "https://93.184.216.36/mcp",
        transport: "streamable_http",
        authType: "bearer",
        secret: "integration-race-secret",
        timeoutSeconds: 10,
        resultMaxBytes: 16_384,
        networkPolicy: "public_only",
        allowedHosts: ["93.184.216.36"],
        tools: [{
          name: "race.read",
          safetyClass: "read_only",
          requiresConfirmation: false,
        }],
      },
      { principal, requestId: "integration-remote-mcp-race-create" },
    );
    await Promise.allSettled([
      remoteMcp.updateEndpoint(
        credentialRaceEndpoint.id,
        { secretAction: "clear" },
        { principal, requestId: "integration-remote-mcp-race-clear" },
      ),
      remoteMcp.updateEndpoint(
        credentialRaceEndpoint.id,
        { enabled: true, secretAction: "keep" },
        { principal, requestId: "integration-remote-mcp-race-enable" },
      ),
    ]);
    await expect(
      remoteMcp.endpoint(principal.tenantId, credentialRaceEndpoint.id),
    ).resolves.toMatchObject({ enabled: false, secretConfigured: false });
    const published = await store.publishAgent(agent.id, {
      principal,
      requestId: "integration-memory-mcp-publish",
    });
    await store.assignDeviceAgent(device.id, agent.id, {
      principal,
      requestId: "integration-memory-device-agent",
    });
    await expect(store.getAgentConfig(agent.id, published.publishedVersion)).resolves.toMatchObject({
      memoryPolicy: { enabled: true, consent: true, storeMessages: true, storeFacts: true },
      remoteMcpEndpoints: [{
        endpointId: endpoint.id,
        toolNames: ["weather.current"],
        timeoutSeconds: 8,
      }],
    });

    const sessionId = "session_integration_memory_01";
    const turnId = "turn_integration_memory_01";
    await expect(memory.appendMessages(
      agent.id,
      device.id,
      published.publishedVersion,
      [
        {
          idempotencyKey: `${sessionId}:${turnId}:user`,
          sessionId,
          turnId,
          role: "user",
          content: "Tôi thích trà sen.",
          occurredAt: "2026-07-29T04:00:00.000Z",
        },
        {
          idempotencyKey: `${sessionId}:${turnId}:assistant`,
          sessionId,
          turnId,
          role: "assistant",
          content: "Mình sẽ ghi nhớ sở thích đó.",
          occurredAt: "2026-07-29T04:00:01.000Z",
        },
      ],
    )).resolves.toEqual({ accepted: 2, duplicates: 0 });
    await expect(memory.appendMessages(
      agent.id,
      device.id,
      published.publishedVersion,
      [{
        idempotencyKey: `${sessionId}:${turnId}:user`,
        sessionId,
        turnId,
        role: "user",
        content: "Tôi thích trà sen.",
        occurredAt: "2026-07-29T04:00:00.000Z",
      }],
    )).resolves.toEqual({ accepted: 0, duplicates: 1 });
    const nextTurnId = "turn_integration_memory_02";
    await expect(memory.appendMessages(
      agent.id,
      device.id,
      published.publishedVersion,
      [
        {
          idempotencyKey: `${sessionId}:${nextTurnId}:user`,
          sessionId,
          turnId: nextTurnId,
          role: "user",
          content: "Tôi thường uống vào buổi sáng.",
          occurredAt: "2026-07-29T04:01:00.000Z",
        },
        {
          idempotencyKey: `${sessionId}:${nextTurnId}:assistant`,
          sessionId,
          turnId: nextTurnId,
          role: "assistant",
          content: "Mình hiểu rồi.",
          occurredAt: "2026-07-29T04:01:01.000Z",
        },
      ],
    )).resolves.toEqual({ accepted: 2, duplicates: 0 });
    await expect(memory.appendMessages(
      agent.id,
      device.id,
      published.publishedVersion,
      [{
        idempotencyKey: `${sessionId}:${turnId}:user`,
        sessionId,
        turnId,
        role: "user",
        content: "Tôi thích trà sen.",
        occurredAt: "2026-07-29T04:00:00.000Z",
      }],
    )).resolves.toEqual({ accepted: 0, duplicates: 1 });
    await expect(memory.upsertFacts(
      agent.id,
      device.id,
      published.publishedVersion,
      [{
        idempotencyKey: `${sessionId}:${turnId}:fact:favorite_drink`,
        category: "preference",
        key: "favorite_drink",
        value: "Trà sen",
        confidence: 0.94,
        sourceSessionId: sessionId,
        sourceTurnId: turnId,
        expiresInDays: 90,
      }],
    )).resolves.toEqual({ accepted: 1, duplicates: 0, rejected: 0 });
    await expect(memory.upsertFacts(
      agent.id,
      device.id,
      published.publishedVersion,
      [{
        idempotencyKey: `${sessionId}:${nextTurnId}:fact:favorite_drink`,
        category: "preference",
        key: "favorite_drink",
        value: "Cà phê",
        confidence: 0.8,
        sourceSessionId: sessionId,
        sourceTurnId: nextTurnId,
        expiresInDays: 90,
      }],
    )).resolves.toEqual({ accepted: 1, duplicates: 0, rejected: 0 });
    await expect(memory.upsertFacts(
      agent.id,
      device.id,
      published.publishedVersion,
      [{
        idempotencyKey: `${sessionId}:${turnId}:fact:favorite_drink`,
        category: "preference",
        key: "favorite_drink",
        value: "Trà sen",
        confidence: 0.94,
        sourceSessionId: sessionId,
        sourceTurnId: turnId,
        expiresInDays: 90,
      }],
    )).resolves.toEqual({ accepted: 0, duplicates: 1, rejected: 0 });
    await expect(memory.getContext(
      agent.id,
      device.id,
      published.publishedVersion,
    )).resolves.toMatchObject({
      messages: [
        { role: "user", content: "Tôi thường uống vào buổi sáng." },
        { role: "assistant", content: "Mình hiểu rồi." },
      ],
      memoryFacts: [{ category: "preference", key: "favorite_drink", value: "Cà phê" }],
    });
    await expect(memory.listMessages(
      "other-tenant",
      agent.id,
      device.id,
      50,
    )).rejects.toThrow(/agent not found/i);
    const exported = await memory.exportMemory(agent.id, device.id, {
      principal,
      requestId: "integration-memory-export",
    });
    expect(exported).toMatchObject({
      version: 1,
      agentId: agent.id,
      deviceId: device.id,
      messages: [
        { role: "user", content: "Tôi thường uống vào buổi sáng." },
        { role: "assistant", content: "Mình hiểu rồi." },
      ],
      facts: [{ category: "preference", key: "favorite_drink", value: "Cà phê" }],
    });
    const exportAudit = await prisma.auditEvent.findFirst({
      where: { tenantId: principal.tenantId, requestId: "integration-memory-export" },
    });
    expect(exportAudit).toMatchObject({ action: "memory.export.create", targetId: device.id });
    expect(JSON.stringify(exportAudit?.details)).not.toContain("Cà phê");

    const resolved = await remoteMcp.resolve(
      agent.id,
      device.id,
      published.publishedVersion,
    );
    expect(resolved).toMatchObject({
      configVersion: published.publishedVersion,
      endpoints: [{
        id: endpoint.id,
        timeoutSeconds: 8,
        allowedTools: [{ name: "weather.current" }],
      }],
    });
    expect(resolved.endpoints[0]?.headers).toEqual({
      Authorization: "Bearer integration-remote-mcp-secret",
    });
    await expect(remoteMcp.recordInvocation({
      eventId: "750dcf83-4f54-4c02-85dd-76ef31dc0823",
      endpointId: endpoint.id,
      agentId: agent.id,
      deviceId: device.id,
      configVersion: published.publishedVersion,
      sessionId,
      turnId,
      toolName: "weather.current",
      argumentsHash: "a".repeat(64),
      status: "succeeded",
      durationMs: 120,
      actor: "model",
      occurredAt: "2026-07-29T04:00:02.000Z",
    })).resolves.toEqual({ recorded: true, duplicate: false });
    await expect(remoteMcp.recordInvocation({
      eventId: "750dcf83-4f54-4c02-85dd-76ef31dc0823",
      endpointId: endpoint.id,
      agentId: agent.id,
      deviceId: device.id,
      configVersion: published.publishedVersion,
      sessionId,
      turnId,
      toolName: "weather.current",
      argumentsHash: "a".repeat(64),
      status: "succeeded",
      durationMs: 120,
      actor: "model",
      occurredAt: "2026-07-29T04:00:02.000Z",
    })).resolves.toEqual({ recorded: false, duplicate: true });
    await remoteMcp.updateEndpoint(
      endpoint.id,
      { secretAction: "rotate", secret: "integration-remote-mcp-secret-rotated" },
      { principal, requestId: "integration-remote-mcp-rotate" },
    );
    await expect(remoteMcp.resolve(
      agent.id,
      device.id,
      published.publishedVersion,
    )).resolves.toMatchObject({
      endpoints: [{ headers: { Authorization: "Bearer integration-remote-mcp-secret-rotated" } }],
    });
    await remoteMcp.updateEndpoint(
      endpoint.id,
      { enabled: false },
      { principal, requestId: "integration-remote-mcp-disable" },
    );
    await expect(remoteMcp.resolve(
      agent.id,
      device.id,
      published.publishedVersion,
    )).resolves.toMatchObject({ endpoints: [] });
    const cascadeInvocationId = "2aa9c84b-9dd0-4092-92cb-876294807179";
    await prisma.remoteMcpInvocation.create({
      data: {
        id: cascadeInvocationId,
        tenantId: principal.tenantId,
        endpointId: credentialRaceEndpoint.id,
        agentId: agent.id,
        deviceId: device.id,
        configVersion: published.publishedVersion,
        sessionId,
        turnId,
        toolName: "race.read",
        argumentsHash: "b".repeat(64),
        status: RemoteMcpCallStatus.SUCCEEDED,
        durationMs: 1,
        actor: RemoteMcpCallActor.SYSTEM,
        occurredAt: new Date("2026-07-29T04:00:03.000Z"),
      },
    });
    await prisma.remoteMcpEndpoint.delete({ where: { id: credentialRaceEndpoint.id } });
    await expect(
      prisma.remoteMcpInvocation.findUnique({ where: { id: cascadeInvocationId } }),
    ).resolves.toBeNull();
    const importedAt = new Date();
    const oldImportedAt = new Date(importedAt.getTime() - 3 * 86_400_000);
    const futureImportedAt = new Date(importedAt.getTime() + 30 * 86_400_000);
    await prisma.conversationMemoryMessage.createMany({
      data: [
        {
          idempotencyKey: `${sessionId}:old-import:user`,
          tenantId: principal.tenantId,
          agentId: agent.id,
          deviceId: device.id,
          sessionId,
          turnId: "old-import",
          role: MemoryMessageRole.USER,
          content: "Bản ghi cũ phải hết hạn theo thời điểm xảy ra.",
          occurredAt: oldImportedAt,
          retentionUntil: futureImportedAt,
          createdAt: oldImportedAt,
        },
        {
          idempotencyKey: `${sessionId}:future-import:user`,
          tenantId: principal.tenantId,
          agentId: agent.id,
          deviceId: device.id,
          sessionId,
          turnId: "future-import",
          role: MemoryMessageRole.USER,
          content: "Timestamp tương lai không được chiếm lịch sử mãi mãi.",
          occurredAt: futureImportedAt,
          retentionUntil: futureImportedAt,
          createdAt: importedAt,
        },
      ],
    });
    await memory.upsertFacts(
      agent.id,
      device.id,
      published.publishedVersion,
      [
        {
          idempotencyKey: `${sessionId}:fact:city`,
          category: "profile",
          key: "city",
          value: "Hà Nội",
          confidence: 0.9,
          sourceSessionId: sessionId,
          sourceTurnId: nextTurnId,
          expiresInDays: 90,
        },
        {
          idempotencyKey: `${sessionId}:fact:color`,
          category: "preference",
          key: "favorite_color",
          value: "Xanh dương",
          confidence: 0.85,
          sourceSessionId: sessionId,
          sourceTurnId: nextTurnId,
          expiresInDays: 90,
        },
      ],
    );
    const currentAgent = (await store.listAgents(principal.tenantId)).find(
      ({ id }) => id === agent.id,
    );
    if (!currentAgent) throw new Error("Integration memory agent disappeared");
    await store.updateAgent(
      agent.id,
      {
        draftConfig: {
          ...currentAgent.draftConfig,
          memoryPolicy: {
            ...currentAgent.draftConfig.memoryPolicy as Record<string, unknown>,
            enabled: false,
            consent: false,
            retentionDays: 1,
            maxMessages: 2,
            factRetentionDays: 1,
            maxFacts: 1,
          },
        },
      },
      { principal, requestId: "integration-memory-revoke" },
    );
    await store.publishAgent(agent.id, {
      principal,
      requestId: "integration-memory-revoke-publish",
    });
    await expect(memory.appendMessages(
      agent.id,
      device.id,
      published.publishedVersion,
      [{
        idempotencyKey: `${sessionId}:turn_after_revoke:user`,
        sessionId,
        turnId: "turn_after_revoke",
        role: "user",
        content: "Không lưu sau khi thu hồi consent.",
        occurredAt: "2026-07-29T04:02:00.000Z",
      }],
    )).rejects.toThrow(/storage is disabled/i);
    await expect(memory.getContext(
      agent.id,
      device.id,
      published.publishedVersion,
    )).resolves.toMatchObject({
      policy: { enabled: false, consent: false },
      messages: [],
      memoryFacts: [],
    });
    const retainedMessages = await prisma.conversationMemoryMessage.findMany({
      where: { tenantId: principal.tenantId, agentId: agent.id, deviceId: device.id },
      orderBy: [{ occurredAt: "desc" }, { id: "desc" }],
    });
    const retainedFacts = await prisma.conversationMemoryFact.findMany({
      where: { tenantId: principal.tenantId, agentId: agent.id, deviceId: device.id },
    });
    expect(retainedMessages).toHaveLength(2);
    expect(retainedFacts).toHaveLength(1);
    expect(retainedMessages.every((message) => (
      message.occurredAt.getTime() <= message.createdAt.getTime() &&
      message.retentionUntil.getTime() <= message.occurredAt.getTime() + 86_400_000
    ))).toBe(true);
    expect(retainedFacts[0]!.expiresAt.getTime()).toBeLessThanOrEqual(
      retainedFacts[0]!.updatedAt.getTime() + 86_400_000,
    );
    await expect(prisma.memoryWriteReceipt.findFirst({
      where: {
        tenantId: principal.tenantId,
        agentId: agent.id,
        deviceId: device.id,
        idempotencyKey: `${sessionId}:${turnId}:user`,
      },
    })).resolves.not.toBeNull();
    await store.assignDeviceAgent(device.id, undefined, {
      principal,
      requestId: "integration-memory-public-unassign",
    });
    await expect(memory.exportMemory(agent.id, device.id, {
      principal,
      requestId: "integration-memory-export-after-unassign",
    })).resolves.toMatchObject({
      messages: [expect.any(Object), expect.any(Object)],
      facts: [expect.any(Object)],
    });
    await expect(memory.purgeMessages(agent.id, device.id, {
      principal,
      requestId: "integration-memory-purge",
    })).resolves.toEqual({ deleted: 2 });
    await store.assignDeviceAgent(device.id, agent.id, {
      principal,
      requestId: "integration-memory-public-reassign",
    });
    expect(updated.draftConfig.memoryPolicy).toMatchObject({ enabled: true });
  });

  it("rejects direct cross-tenant memory/MCP rows and invocation config drift", async () => {
    const [agent] = await store.listAgents(principal.tenantId);
    const [device] = await store.listDevices(principal.tenantId);
    const endpoint = await prisma.remoteMcpEndpoint.findFirst({
      where: { tenantId: principal.tenantId },
    });
    if (!agent || !device || !endpoint) throw new Error("Integration integrity scope missing");
    const otherTenant = await prisma.tenant.create({
      data: { slug: `integrity-${Date.now()}`, name: "Integrity tenant" },
    });
    try {
      const otherAgent = await prisma.agent.create({
        data: {
          tenantId: otherTenant.id,
          name: "Other tenant agent",
          persona: "",
          draftConfig: {},
        },
      });
      await expect(prisma.conversationMemoryMessage.create({
        data: {
          tenantId: otherTenant.id,
          agentId: agent.id,
          deviceId: device.id,
          idempotencyKey: "cross-tenant-memory-message",
          sessionId: "cross_tenant_session_01",
          turnId: "cross_tenant_turn_01",
          role: MemoryMessageRole.USER,
          content: "must reject",
          occurredAt: new Date(),
          retentionUntil: new Date(Date.now() + 86_400_000),
        },
      })).rejects.toThrow();
      await expect(prisma.agentRemoteMcpAssignment.create({
        data: {
          tenantId: otherTenant.id,
          agentId: otherAgent.id,
          endpointId: endpoint.id,
          toolNames: ["weather.current"],
          timeoutMs: 5_000,
        },
      })).rejects.toThrow();
      await expect(prisma.remoteMcpInvocation.create({
        data: {
          id: "8e3f29d2-fbdd-4e83-b987-25a8af1c0f14",
          tenantId: principal.tenantId,
          endpointId: endpoint.id,
          agentId: agent.id,
          deviceId: device.id,
          configVersion: 2_147_483_647,
          sessionId: "integrity_session_01",
          turnId: "integrity_turn_01",
          toolName: "weather.current",
          argumentsHash: "c".repeat(64),
          status: RemoteMcpCallStatus.FAILED,
          durationMs: 1,
          actor: RemoteMcpCallActor.SYSTEM,
          occurredAt: new Date(),
        },
      })).rejects.toThrow();
    } finally {
      await prisma.tenant.delete({ where: { id: otherTenant.id } });
    }
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
