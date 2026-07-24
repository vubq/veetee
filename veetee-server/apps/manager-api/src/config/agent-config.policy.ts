import { BadRequestException } from "@nestjs/common";

import {
  PERSONALITY_PRESETS,
  validateAgentPromptDraft,
  type PersonalityPreset,
} from "./agent-prompt.policy.js";

const providerKinds = ["vad", "asr", "llm", "tts", "realtime", "memory"] as const;
const requiredCascadeKinds = ["vad", "asr", "llm", "tts"] as const;

export type ProviderKindValue = (typeof providerKinds)[number];

export interface ProviderPolicyBinding {
  id: string;
  kind: ProviderKindValue;
  adapter: string;
  model: string;
  baseUrl?: string;
  config?: Record<string, unknown>;
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
  // Zero disables the product-level ceiling. Provider deadlines remain separate.
  maxSessionSeconds: [0, 3_600],
  totalTurnSeconds: [0, 60],
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

/**
 * Provider settings are intentionally data-only. Secrets stay in the encrypted
 * column and provider adapters decide how the validated, bounded values map to
 * their SDK/API request.
 */
export function validateProviderConfig(
  kind: ProviderKindValue,
  adapter: string,
  value: unknown,
): Record<string, unknown> {
  if (value === undefined) return {};
  if (!isRecord(value)) {
    throw new BadRequestException("Provider config must be an object");
  }
  const keys = Object.keys(value);
  if (keys.length > 48) {
    throw new BadRequestException("Provider config may contain at most 48 keys");
  }
  const normalized: Record<string, unknown> = {};
  for (const key of keys) {
    if (!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(key)) {
      throw new BadRequestException(`Provider config key ${key} is invalid`);
    }
    if (
      /^(secret|token|password|api[_-]?key|access[_-]?token)$/i.test(key) ||
      /(^|_)(secret|token|password)(_|$)/i.test(key)
    ) {
      throw new BadRequestException(`Provider config key ${key} must stay in the encrypted secret field`);
    }
    const item = value[key];
    validateProviderConfigValue(item, key, 1);
    normalized[key] = item;
  }

  if (kind === "llm" && (adapter === "groq-cloud" || adapter.includes("groq"))) {
    boundedNumber(normalized, "temperature", 0, 2);
    boundedNumber(normalized, "topP", 0, 1);
    boundedInteger(normalized, "maxCompletionTokens", 64, 16_384);
    boundedEnum(normalized, "serviceTier", ["auto", "on_demand", "flex", "performance"]);
    boundedEnum(normalized, "reasoningEffort", ["none", "default", "low", "medium", "high"]);
    boundedBoolean(normalized, "parallelToolCalls");
    boundedBoolean(normalized, "streamProseResponse");
    boundedEnum(normalized, "responseFormat", ["auto", "json_object", "json_schema"]);
  }
  if (kind === "tts") {
    boundedNumber(normalized, "rate", 0.5, 2.0);
    boundedNumber(normalized, "pitchHz", -100, 100);
    boundedNumber(normalized, "volume", 0, 1.5);
    boundedInteger(normalized, "outputSampleRate", 8_000, 48_000);
    boundedBoolean(normalized, "supportsPitch");
    if (normalized.voice !== undefined) boundedString(normalized, "voice", 1, 160);
    if (normalized.voiceId !== undefined) boundedString(normalized, "voiceId", 1, 160);
    if (normalized.gender !== undefined) {
      boundedEnum(normalized, "gender", ["female", "male", "neutral"]);
    }
    const voices = normalized.voices;
    if (voices !== undefined) {
      if (!Array.isArray(voices) || voices.length > 256) {
        throw new BadRequestException("Provider config voices must contain at most 256 entries");
      }
      for (const [index, voice] of voices.entries()) {
        if (!isRecord(voice)) {
          throw new BadRequestException(`Provider config voices[${index}] must be an object`);
        }
        boundedString(voice, "id", 1, 160);
        boundedString(voice, "label", 1, 160);
        boundedString(voice, "locale", 2, 35);
        if (voice.gender !== undefined) boundedEnum(voice, "gender", ["female", "male", "neutral"]);
      }
    }
  }
  if (normalized.endpoint !== undefined) {
    if (typeof normalized.endpoint !== "string" || normalized.endpoint.length > 512) {
      throw new BadRequestException("Provider config endpoint must be a short URL string");
    }
  }
  return normalized;
}

function validateProviderConfigValue(value: unknown, path: string, depth: number): void {
  if (depth > 4) {
    throw new BadRequestException(`Provider config ${path} is nested too deeply`);
  }
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    if (value.length > 4_096) {
      throw new BadRequestException(`Provider config ${path} is too long`);
    }
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new BadRequestException(`Provider config ${path} must be finite`);
    }
    return;
  }
  if (Array.isArray(value)) {
    if (value.length > 256) {
      throw new BadRequestException(`Provider config ${path} contains too many entries`);
    }
    value.forEach((item, index) =>
      validateProviderConfigValue(item, `${path}[${index}]`, depth + 1),
    );
    return;
  }
  if (!isRecord(value) || Object.keys(value).length > 64) {
    throw new BadRequestException(`Provider config ${path} has an unsupported value`);
  }
  for (const [key, item] of Object.entries(value)) {
    if (!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(key)) {
      throw new BadRequestException(`Provider config key ${path}.${key} is invalid`);
    }
    if (
      /^(secret|token|password|api[_-]?key|access[_-]?token)$/i.test(key) ||
      /(^|_)(secret|token|password)(_|$)/i.test(key)
    ) {
      throw new BadRequestException(
        `Provider config key ${path}.${key} must stay in the encrypted secret field`,
      );
    }
    validateProviderConfigValue(item, `${path}.${key}`, depth + 1);
  }
}

function boundedString(
  object: Record<string, unknown>,
  key: string,
  minimum: number,
  maximum: number,
): void {
  const value = object[key];
  if (typeof value !== "string" || value.trim().length < minimum || value.length > maximum) {
    throw new BadRequestException(`Provider config ${key} must contain ${minimum} to ${maximum} characters`);
  }
  object[key] = value.trim();
}

function boundedNumber(
  object: Record<string, unknown>,
  key: string,
  minimum: number,
  maximum: number,
): void {
  const value = object[key];
  if (value === undefined) return;
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    throw new BadRequestException(`Provider config ${key} must be between ${minimum} and ${maximum}`);
  }
}

function boundedInteger(
  object: Record<string, unknown>,
  key: string,
  minimum: number,
  maximum: number,
): void {
  const value = object[key];
  if (value === undefined) return;
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum || value > maximum) {
    throw new BadRequestException(`Provider config ${key} must be an integer from ${minimum} to ${maximum}`);
  }
}

function boundedBoolean(object: Record<string, unknown>, key: string): void {
  const value = object[key];
  if (value !== undefined && typeof value !== "boolean") {
    throw new BadRequestException(`Provider config ${key} must be boolean`);
  }
}

function boundedEnum(
  object: Record<string, unknown>,
  key: string,
  choices: readonly string[],
): void {
  const value = object[key];
  if (value !== undefined && (typeof value !== "string" || !choices.includes(value))) {
    throw new BadRequestException(`Provider config ${key} is invalid`);
  }
}

export function validateAgentVoiceConfig(
  config: Record<string, unknown>,
  ttsProviders: readonly ProviderPolicyBinding[],
  locale: string,
): Record<string, unknown> | undefined {
  const raw = config.voice;
  if (raw === undefined) return undefined;
  if (!isRecord(raw)) throw new BadRequestException("Agent voice must be an object");
  const providerId = raw.providerId;
  const voiceId = raw.voiceId;
  if (typeof providerId !== "string" || !providerId) {
    throw new BadRequestException("Agent voice.providerId is required");
  }
  if (typeof voiceId !== "string" || !voiceId.trim() || voiceId.length > 160) {
    throw new BadRequestException("Agent voice.voiceId must contain 1 to 160 characters");
  }
  const provider = ttsProviders.find((candidate) => candidate.id === providerId);
  if (!provider) throw new BadRequestException("Agent voice provider must be in the TTS chain");
  if (!provider.locales.includes("*") && !provider.locales.includes(locale)) {
    throw new BadRequestException(`Agent voice provider ${providerId} does not support locale ${locale}`);
  }
  const catalog = provider.config?.voices;
  if (Array.isArray(catalog)) {
    const catalogIds = catalog
      .map((voice) => isRecord(voice) ? voice.id : undefined)
      .filter((id): id is string => typeof id === "string");
    if (catalogIds.length > 0 && !catalogIds.includes(voiceId.trim())) {
      throw new BadRequestException(`Agent voice ${voiceId} is not in the provider catalog`);
    }
  }
  const gender = raw.gender;
  if (gender !== undefined && (gender !== "female" && gender !== "male" && gender !== "neutral")) {
    throw new BadRequestException("Agent voice.gender is invalid");
  }
  for (const [key, [minimum, maximum]] of Object.entries({
    rate: [0.5, 2],
    pitchHz: [-100, 100],
    volume: [0, 1.5],
  } as const)) {
    const value = raw[key];
    if (value !== undefined && (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum)) {
      throw new BadRequestException(`Agent voice.${key} is outside the supported range`);
    }
  }
  if (raw.pitchHz !== undefined && raw.pitchHz !== 0 && provider.config?.supportsPitch === false) {
    throw new BadRequestException("Selected TTS provider does not support pitch adjustment");
  }
  return {
    providerId,
    voiceId: voiceId.trim(),
    ...(gender !== undefined ? { gender } : {}),
    ...(raw.rate !== undefined ? { rate: raw.rate } : {}),
    ...(raw.pitchHz !== undefined ? { pitchHz: raw.pitchHz } : {}),
    ...(raw.volume !== undefined ? { volume: raw.volume } : {}),
  };
}

export function validateAgentDraftConfig(
  config: Record<string, unknown>,
  personalityPresets: readonly PersonalityPreset[] = PERSONALITY_PRESETS,
): void {
  validateAgentPromptDraft(config.prompt, personalityPresets);
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
