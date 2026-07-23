import { BadRequestException } from "@nestjs/common";

import { validateAgentPromptDraft } from "./agent-prompt.policy.js";

const providerKinds = ["vad", "asr", "llm", "tts", "realtime", "memory"] as const;
const requiredCascadeKinds = ["vad", "asr", "llm", "tts"] as const;

export type ProviderKindValue = (typeof providerKinds)[number];

export interface ProviderPolicyBinding {
  id: string;
  kind: ProviderKindValue;
  adapter: string;
  model: string;
  baseUrl?: string;
  secretConfigured: boolean;
  enabled: boolean;
  priority: number;
  locales: string[];
}

export interface ExpandedProviderChain {
  kind: ProviderKindValue;
  locale: string;
  providers: ProviderPolicyBinding[];
}

const conversationNumberBounds = {
  firstInputSeconds: [3, 300],
  betweenTurnsSeconds: [3, 600],
  closingGraceSeconds: [0.5, 60],
  maxSessionSeconds: [10, 3_600],
  totalTurnSeconds: [5, 60],
  admissionSeconds: [0.1, 5],
  plannerSeconds: [0.5, 15],
  llmSeconds: [1, 45],
  ttsSeconds: [1, 30],
  mcpSeconds: [0.5, 30],
  contextMessageLimit: [2, 32],
  contextMessageCharacters: [128, 4_000],
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function validateAgentDraftConfig(config: Record<string, unknown>): void {
  validateAgentPromptDraft(config.prompt);
  const conversation = config.conversation;
  if (conversation === undefined) return;
  if (!isRecord(conversation)) {
    throw new BadRequestException("Agent conversation config must be an object");
  }
  for (const [field, [minimum, maximum]] of Object.entries(conversationNumberBounds)) {
    const value = conversation[field];
    if (value === undefined) continue;
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < minimum ||
      value > maximum
    ) {
      throw new BadRequestException(
        `Agent conversation ${field} must be between ${minimum} and ${maximum}`,
      );
    }
  }
  const goodbye = conversation.timeoutGoodbye;
  if (
    goodbye !== undefined &&
    (typeof goodbye !== "string" || !goodbye.trim() || goodbye.length > 240)
  ) {
    throw new BadRequestException(
      "Agent conversation timeoutGoodbye must contain 1 to 240 characters",
    );
  }
}

export function expandProviderChains(
  config: Record<string, unknown>,
  availableProviders: ProviderPolicyBinding[],
  defaultLocale: string,
  interactionMode: "auto" | "manual" | "realtime",
): ExpandedProviderChain[] {
  const rawChains = config.providerChains;
  if (!Array.isArray(rawChains) || rawChains.length === 0 || rawChains.length > 32) {
    throw new BadRequestException("Agent providerChains must contain 1 to 32 explicit chains");
  }
  const providersById = new Map(availableProviders.map((provider) => [provider.id, provider]));
  const seenChains = new Set<string>();
  const expanded = rawChains.map((value, index) => {
    if (!isRecord(value)) {
      throw new BadRequestException(`Agent providerChains[${index}] must be an object`);
    }
    const kind = value.kind;
    const locale = value.locale;
    const providerIds = value.providerIds;
    if (!providerKinds.includes(kind as ProviderKindValue)) {
      throw new BadRequestException(`Agent providerChains[${index}].kind is invalid`);
    }
    if (typeof locale !== "string" || !isLocaleOrWildcard(locale)) {
      throw new BadRequestException(`Agent providerChains[${index}].locale is invalid`);
    }
    if (!Array.isArray(providerIds) || providerIds.length === 0 || providerIds.length > 4) {
      throw new BadRequestException(
        `Agent providerChains[${index}].providerIds must contain 1 to 4 providers`,
      );
    }
    if (new Set(providerIds).size !== providerIds.length) {
      throw new BadRequestException(`Agent providerChains[${index}] contains duplicate providers`);
    }
    const chainKey = `${kind}:${locale}`;
    if (seenChains.has(chainKey)) {
      throw new BadRequestException(`Agent provider chain ${chainKey} is duplicated`);
    }
    seenChains.add(chainKey);
    const providers = providerIds.map((providerId) => {
      if (typeof providerId !== "string") {
        throw new BadRequestException(`Agent providerChains[${index}] has an invalid provider id`);
      }
      const provider = providersById.get(providerId);
      if (!provider || !provider.enabled) {
        throw new BadRequestException(`Provider ${providerId} is missing or disabled`);
      }
      if (provider.kind !== kind) {
        throw new BadRequestException(`Provider ${providerId} does not match chain kind ${kind}`);
      }
      if (!provider.locales.includes("*") && !provider.locales.includes(locale)) {
        throw new BadRequestException(`Provider ${providerId} does not support locale ${locale}`);
      }
      return provider;
    });
    return { kind: kind as ProviderKindValue, locale, providers };
  });

  const requiredKinds = interactionMode === "realtime" ? (["realtime"] as const) : requiredCascadeKinds;
  for (const kind of requiredKinds) {
    if (!expanded.some((chain) => chain.kind === kind && [defaultLocale, "*"].includes(chain.locale))) {
      throw new BadRequestException(
        `Agent requires an explicit ${kind} provider chain for ${defaultLocale} or *`,
      );
    }
  }
  return expanded;
}

function isLocaleOrWildcard(value: string): boolean {
  if (value === "*") return true;
  try {
    return new Intl.Locale(value).toString() === value;
  } catch {
    return false;
  }
}
