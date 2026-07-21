import { Algorithm, hash } from "@node-rs/argon2";
import {
  InteractionMode,
  Prisma,
  PrismaClient,
  ProviderHealth,
  ProviderKind,
  TenantRole,
} from "@prisma/client";

export interface SeedInput {
  tenantSlug: string;
  tenantName: string;
  adminEmail: string;
  adminPassword: string;
  adminName: string;
}

export async function seedControlPlane(prisma: PrismaClient, input: SeedInput): Promise<void> {
  const passwordHash = await hash(input.adminPassword, {
    algorithm: Algorithm.Argon2id,
    memoryCost: 19_456,
    timeCost: 2,
    parallelism: 1,
  });
  const tenant = await prisma.tenant.upsert({
    where: { slug: input.tenantSlug },
    update: { name: input.tenantName },
    create: { slug: input.tenantSlug, name: input.tenantName },
  });
  const user = await prisma.user.upsert({
    where: { email: input.adminEmail.toLowerCase() },
    update: { displayName: input.adminName, active: true, passwordHash },
    create: {
      email: input.adminEmail.toLowerCase(),
      displayName: input.adminName,
      passwordHash,
    },
  });
  await prisma.membership.upsert({
    where: { tenantId_userId: { tenantId: tenant.id, userId: user.id } },
    update: { role: TenantRole.OWNER },
    create: { tenantId: tenant.id, userId: user.id, role: TenantRole.OWNER },
  });

  const agent = await prisma.agent.upsert({
    where: { tenantId_name: { tenantId: tenant.id, name: "Veetee Việt" } },
    update: {},
    create: {
      tenantId: tenant.id,
      name: "Veetee Việt",
      defaultLocale: "vi-VN",
      interactionMode: InteractionMode.AUTO,
      persona: "Robot AI thân thiện, rõ ràng và ưu tiên tiếng Việt.",
      version: 1,
      publishedVersion: 1,
      draftConfig: defaultAgentConfig() as Prisma.InputJsonValue,
    },
  });
  await prisma.agentConfigVersion.upsert({
    where: { agentId_version: { agentId: agent.id, version: 1 } },
    update: {
      snapshot: agentSnapshot(agent.id, defaultAgentConfig()) as Prisma.InputJsonValue,
    },
    create: {
      agentId: agent.id,
      version: 1,
      snapshot: agentSnapshot(agent.id, defaultAgentConfig()) as Prisma.InputJsonValue,
    },
  });

  const providers = [
    [ProviderKind.VAD, "silero-local", "silero-vad", null, true],
    [ProviderKind.ASR, "sherpa-onnx", "zipformer-vi-30m-int8", null, true],
    [
      ProviderKind.LLM,
      "openai-compatible-9router",
      "cx/gpt-5.4-mini",
      "http://127.0.0.1:20128/v1",
      false,
    ],
    [ProviderKind.TTS, "vieneu-local", "vieneu-tts-v3-turbo", null, true],
  ] as const;
  for (const [kind, adapter, model, baseUrl, secretConfigured] of providers) {
    await prisma.providerBinding.upsert({
      where: { tenantId_kind_adapter_model: { tenantId: tenant.id, kind, adapter, model } },
      update: {},
      create: {
        tenantId: tenant.id,
        kind,
        adapter,
        model,
        baseUrl,
        secretConfigured,
        enabled: true,
        health: ProviderHealth.UNKNOWN,
      },
    });
  }
}

export function defaultAgentConfig(): Record<string, unknown> {
  return {
    schemaVersion: 1,
    locale: "vi-VN",
    interactionMode: "auto",
    conversation: {
      firstInputSeconds: 15,
      betweenTurnsSeconds: 30,
      closingGraceSeconds: 5,
      totalTurnSeconds: 30,
      admissionSeconds: 1,
      plannerSeconds: 4,
      llmSeconds: 20,
      ttsSeconds: 10,
      mcpSeconds: 10,
    },
  };
}

export function agentSnapshot(
  agentId: string,
  config: Record<string, unknown>,
): Record<string, unknown> {
  return {
    ...config,
    agentId,
    version: 1,
    defaultLocale: "vi-VN",
    interactionMode: "auto",
    persona: "Robot AI thân thiện, rõ ràng và ưu tiên tiếng Việt.",
    providers: [
      { kind: "vad", adapter: "silero-local", model: "silero-vad" },
      { kind: "asr", adapter: "sherpa-onnx", model: "zipformer-vi-30m-int8" },
      {
        kind: "llm",
        adapter: "openai-compatible-9router",
        model: "cx/gpt-5.4-mini",
        baseUrl: "http://127.0.0.1:20128/v1",
      },
      { kind: "tts", adapter: "vieneu-local", model: "vieneu-tts-v3-turbo" },
    ],
  };
}
