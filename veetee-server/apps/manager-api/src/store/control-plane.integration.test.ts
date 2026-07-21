import { Algorithm, hash } from "@node-rs/argon2";
import { TenantRole } from "@prisma/client";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { AuditService } from "../audit/audit.service.js";
import { AuthService } from "../auth/auth.service.js";
import type { Principal } from "../auth/auth.types.js";
import { PrismaService } from "../database/prisma.service.js";
import { RedisService } from "../database/redis.service.js";
import { PairingService } from "../pairing/pairing.service.js";
import { SecretCryptoService } from "../security/secret-crypto.service.js";
import { ControlPlaneStore } from "./control-plane.store.js";

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
  let principal: Principal;

  beforeAll(async () => {
    process.env.VEETEE_MASTER_KEY = Buffer.alloc(32, 7).toString("base64");
    process.env.VEETEE_AUTH_SECRET = "integration-auth-secret-with-at-least-32-characters";
    await prisma.$connect();
    await redis.client.connect();
    await redis.client.flushdb();
    await prisma.auditEvent.deleteMany();
    await prisma.refreshSession.deleteMany();
    await prisma.deviceReportedState.deleteMany();
    await prisma.deviceDesiredState.deleteMany();
    await prisma.device.deleteMany();
    await prisma.agentConfigVersion.deleteMany();
    await prisma.agent.deleteMany();
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
    const agent = await store.createAgent(
      {
        name: "Integration Agent",
        defaultLocale: "vi-VN",
        interactionMode: "auto",
        persona: "Vietnamese integration agent",
      },
      { principal, requestId: "integration-agent" },
    );
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
    const desired = await store.setDesiredState(
      device.id,
      { agentConfigVersion: published.publishedVersion, resourceBundleVersion: "1.0.0" },
      { principal, requestId: "integration-desired" },
    );
    expect(desired.desiredState.version).toBe(2);
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
      store.updateReportedState(device.id, 4, { marker: "newest" }),
      store.updateReportedState(device.id, 3, { marker: "older" }),
    ]);
    const concurrent = await store.updateReportedState(device.id, 4, { unexpected: true });
    expect(concurrent.reportedState).toMatchObject({
      version: 4,
      state: { marker: "newest" },
    });
    await expect(store.updateReportedState(device.id, 1, {})).rejects.toThrow(/stale/i);
    await expect(store.getAgentConfig(agent.id, published.publishedVersion)).resolves.toMatchObject({
      agentId: agent.id,
      interactionMode: "auto",
    });
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
});
