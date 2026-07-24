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
    [
      ProviderKind.LLM,
      "groq-cloud",
      "llama-3.3-70b-versatile",
      "https://api.groq.com/openai/v1",
    ],
    [ProviderKind.TTS, "edge-tts", "edge-tts-cloud", null],
  ] as const;
  for (const [kind, adapter, model, baseUrl] of providers) {
    const defaultConfig = defaultProviderConfig(adapter);
    const persisted = await prisma.providerBinding.upsert({
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
        config: defaultConfig as Prisma.InputJsonValue,
      },
    });
    const currentConfig =
      persisted.config && typeof persisted.config === "object" && !Array.isArray(persisted.config)
        ? persisted.config as Record<string, unknown>
        : {};
    const defaultVoices = Array.isArray(defaultConfig.voices) ? defaultConfig.voices : [];
    const currentVoices = Array.isArray(currentConfig.voices) ? currentConfig.voices : [];
    const currentVoiceIds = new Set(
      currentVoices
        .map((voice) =>
          voice && typeof voice === "object" && !Array.isArray(voice)
            ? (voice as Record<string, unknown>).id
            : undefined,
        )
        .filter((id): id is string => typeof id === "string"),
    );
    const authoritativeVoices = ["edge-tts", "vieneu-local"].includes(adapter);
    const hasMissingConfig =
      Object.keys(defaultConfig).some((key) => currentConfig[key] === undefined) ||
      defaultVoices.some(
        (voice) =>
          voice &&
          typeof voice === "object" &&
          !Array.isArray(voice) &&
          !currentVoiceIds.has(String((voice as Record<string, unknown>).id)),
      );
    const hasAuthoritativeVoiceDrift = hasAuthoritativeVoiceCatalogDrift(
      currentVoices,
      defaultVoices,
      authoritativeVoices,
    );
    if (hasMissingConfig || hasAuthoritativeVoiceDrift) {
      await prisma.providerBinding.update({
        where: { id: persisted.id },
        data: {
          config: {
            ...defaultConfig,
            ...currentConfig,
            ...(defaultVoices.length
              ? {
                  voices: authoritativeVoices
                    ? defaultVoices
                    : [
                        ...defaultVoices.filter(
                          (voice) =>
                            voice &&
                            typeof voice === "object" &&
                            !Array.isArray(voice) &&
                            !currentVoiceIds.has(String((voice as Record<string, unknown>).id)),
                        ),
                        ...currentVoices,
                      ],
                }
              : {}),
          } as Prisma.InputJsonValue,
        },
      });
    }
  }
  const persistedProviders = await prisma.providerBinding.findMany({
    where: { tenantId: tenant.id, enabled: true },
    orderBy: [{ kind: "asc" }, { priority: "asc" }],
  });
  const providerIds = Object.fromEntries(
    persistedProviders
      .filter((provider) => ["silero-local", "sherpa-onnx", "openai-compatible-9router", "vieneu-local"].includes(provider.adapter))
      .map((provider) => [provider.kind.toLowerCase(), provider.id]),
  );
  const config = defaultAgentConfig(providerIds);
  const ttsProvider = persistedProviders.find((provider) => provider.id === providerIds.tts);
  if (ttsProvider) {
    config.voice = {
      providerId: ttsProvider.id,
      voiceId: "Trúc Ly",
      gender: "female",
      rate: 1.2,
      pitchHz: 0,
      volume: 1,
    };
  }
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
      firstInputSeconds: 180,
      betweenTurnsSeconds: 180,
      closingGraceSeconds: 5,
      maxSessionSeconds: 0,
      totalTurnSeconds: 0,
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

function defaultProviderConfig(adapter: string): Record<string, unknown> {
  if (adapter === "groq-cloud") {
    return {
      serviceTier: "on_demand",
      maxCompletionTokens: 1_024,
      temperature: 0.2,
      topP: 0.95,
      reasoningEffort: "none",
      parallelToolCalls: true,
      responseFormat: "auto",
    };
  }
  if (adapter === "edge-tts") {
    return {
      voice: "vi-VN-HoaiMyNeural",
      gender: "female",
      rate: 1,
      pitchHz: 0,
      volume: 1,
      outputSampleRate: 24_000,
      supportsPitch: true,
      voices: [
        { id: "vi-VN-HoaiMyNeural", label: "Hoài My", locale: "vi-VN", gender: "female" },
        { id: "vi-VN-NamMinhNeural", label: "Nam Minh", locale: "vi-VN", gender: "male" },
      ],
    };
  }
  if (adapter === "vieneu-local") {
    return {
      voice: "Trúc Ly",
      rate: 1.2,
      volume: 1,
      outputSampleRate: 24_000,
      supportsPitch: false,
      voices: [
        { id: "Minh Đức", label: "Minh Đức", locale: "vi-VN", gender: "male" },
        { id: "Phạm Tuyên", label: "Phạm Tuyên", locale: "vi-VN", gender: "male" },
        { id: "Thái Sơn", label: "Thái Sơn", locale: "vi-VN", gender: "male" },
        { id: "Xuân Vĩnh", label: "Xuân Vĩnh", locale: "vi-VN", gender: "male" },
        { id: "Thanh Bình", label: "Thanh Bình", locale: "vi-VN", gender: "male" },
        { id: "Trúc Ly", label: "Trúc Ly", locale: "vi-VN", gender: "female" },
        { id: "Ngọc Linh", label: "Ngọc Linh", locale: "vi-VN", gender: "female" },
        { id: "Đoan Trang", label: "Đoan Trang", locale: "vi-VN", gender: "female" },
        { id: "Mai Anh", label: "Mai Anh", locale: "vi-VN", gender: "female" },
        { id: "Thục Đoan", label: "Thục Đoan", locale: "vi-VN", gender: "female" },
        { id: "Minh Triết", label: "Minh Triết", locale: "vi-VN", gender: "male" },
        { id: "Thùy Dung", label: "Thùy Dung", locale: "vi-VN", gender: "female" },
        { id: "Quang Sơn", label: "Quang Sơn", locale: "vi-VN", gender: "male" },
        { id: "Ngọc Trân", label: "Ngọc Trân", locale: "vi-VN", gender: "female" },
      ],
    };
  }
  return {};
}

export function hasAuthoritativeVoiceCatalogDrift(
  currentVoices: unknown[],
  defaultVoices: unknown[],
  authoritative: boolean,
): boolean {
  return authoritative && JSON.stringify(currentVoices) !== JSON.stringify(defaultVoices);
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
    config: Prisma.JsonValue;
  }> = [],
): Record<string, unknown> {
  const snapshots = providers.map((provider) => ({
    id: provider.id,
    kind: provider.kind.toLowerCase(),
    adapter: provider.adapter,
    model: provider.model,
    ...(provider.baseUrl ? { baseUrl: provider.baseUrl } : {}),
    config: provider.config,
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
