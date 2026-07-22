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
  const providers = [
    [ProviderKind.VAD, "silero-local", "silero-vad", null],
    [ProviderKind.ASR, "sherpa-onnx", "zipformer-vi-30m-int8", null],
    [
      ProviderKind.LLM,
      "openai-compatible-9router",
      "cx/gpt-5.6-terra",
      "http://127.0.0.1:20128/v1",
    ],
    [ProviderKind.TTS, "vieneu-local", "vieneu-tts-v3-turbo", null],
  ] as const;
  for (const [kind, adapter, model, baseUrl] of providers) {
    await prisma.providerBinding.upsert({
      where: { tenantId_kind_adapter_model: { tenantId: tenant.id, kind, adapter, model } },
      update: {},
      create: {
        tenantId: tenant.id,
        kind,
        adapter,
        model,
        baseUrl,
        secretConfigured: false,
        enabled: true,
        priority: 100,
        locales: ["vi-VN"],
        health: ProviderHealth.UNKNOWN,
      },
    });
  }
  const persistedProviders = await prisma.providerBinding.findMany({
    where: { tenantId: tenant.id, enabled: true },
    orderBy: [{ kind: "asc" }, { priority: "asc" }],
  });
  const providerIds = Object.fromEntries(
    persistedProviders.map((provider) => [provider.kind.toLowerCase(), provider.id]),
  );
  const config = defaultAgentConfig(providerIds);
  await prisma.agent.update({
    where: { id: agent.id },
    data: { draftConfig: config as Prisma.InputJsonValue },
  });
  await prisma.agentConfigVersion.upsert({
    where: { agentId_version: { agentId: agent.id, version: 1 } },
    update: {
      snapshot: agentSnapshot(agent.id, config, persistedProviders) as Prisma.InputJsonValue,
    },
    create: {
      agentId: agent.id,
      version: 1,
      snapshot: agentSnapshot(agent.id, config, persistedProviders) as Prisma.InputJsonValue,
    },
  });
}

export function defaultAgentConfig(providerIds: Record<string, string> = {}): Record<string, unknown> {
  return {
    schemaVersion: 1,
    locale: "vi-VN",
    interactionMode: "auto",
    conversation: {
      firstInputSeconds: 15,
      betweenTurnsSeconds: 30,
      closingGraceSeconds: 5,
      maxSessionSeconds: 600,
      totalTurnSeconds: 45,
      admissionSeconds: 1,
      plannerSeconds: 15,
      llmSeconds: 20,
      ttsSeconds: 10,
      mcpSeconds: 10,
    },
    ...(Object.keys(providerIds).length
      ? {
          providerChains: ["vad", "asr", "llm", "tts"].map((kind) => ({
            kind,
            locale: "vi-VN",
            providerIds: [providerIds[kind]],
          })),
        }
      : {}),
  };
}

export function agentSnapshot(
  agentId: string,
  config: Record<string, unknown>,
  providers: Array<{
    id: string;
    kind: ProviderKind;
    adapter: string;
    model: string;
    baseUrl: string | null;
    secretConfigured: boolean;
    priority: number;
    locales: string[];
  }> = [],
): Record<string, unknown> {
  const snapshots = providers.map((provider) => ({
    id: provider.id,
    kind: provider.kind.toLowerCase(),
    adapter: provider.adapter,
    model: provider.model,
    ...(provider.baseUrl ? { baseUrl: provider.baseUrl } : {}),
    secretConfigured: provider.secretConfigured,
    priority: provider.priority,
    locales: provider.locales,
  }));
  const rawChains = Array.isArray(config.providerChains) ? config.providerChains : [];
  return {
    ...config,
    agentId,
    version: 1,
    defaultLocale: "vi-VN",
    interactionMode: "auto",
    persona: "Robot AI thân thiện, rõ ràng và ưu tiên tiếng Việt.",
    providers: snapshots,
    providerChains: rawChains.map((chain) => {
      const value = chain as { kind: string; locale: string; providerIds: string[] };
      return {
        kind: value.kind,
        locale: value.locale,
        providers: value.providerIds
          .map((id) => snapshots.find((provider) => provider.id === id))
          .filter(Boolean),
      };
    }),
  };
}
