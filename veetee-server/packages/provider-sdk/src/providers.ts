import type { AdmissionDecision, Locale, ToolDefinition } from "@veetee/contracts";

import type { ProviderOperationContext } from "./context.js";

export type ProviderKind = "vad" | "asr" | "admission" | "llm" | "tts" | "tool";

export interface ProviderCapabilities {
  readonly providerId: string;
  readonly kind: ProviderKind;
  readonly locales: readonly Locale[];
  readonly streamingInput: boolean;
  readonly streamingOutput: boolean;
  readonly cancellation: "cooperative" | "best_effort";
  readonly toolCalling?: boolean;
  readonly structuredOutput?: boolean;
}

export interface AudioChunk {
  readonly sequence: number;
  readonly sampleRate: number;
  readonly channels: 1;
  readonly encoding: "pcm_s16le" | "opus";
  readonly data: Uint8Array;
  readonly final: boolean;
}

export interface VadEvent {
  readonly type: "speech_start" | "speech_end" | "probability";
  readonly probability: number;
  readonly timestampMs: number;
}

export interface TranscriptEvent {
  readonly text: string;
  readonly locale: Locale;
  readonly final: boolean;
  readonly confidence?: number;
  readonly stability?: number;
}

export interface LlmRequest {
  readonly locale: Locale;
  readonly messages: readonly { role: "system" | "user" | "assistant" | "tool"; content: string }[];
  readonly tools: readonly ToolDefinition[];
}

export type LlmEvent =
  | { readonly type: "text_delta"; readonly text: string }
  | { readonly type: "tool_call"; readonly id: string; readonly name: string; readonly arguments: unknown }
  | { readonly type: "usage"; readonly inputTokens: number; readonly outputTokens: number }
  | { readonly type: "done"; readonly finishReason: string };

export interface TtsRequest {
  readonly text: string;
  readonly locale: Locale;
  readonly voiceId: string;
  readonly sampleRate: number;
}

export interface Provider {
  readonly capabilities: ProviderCapabilities;
  health(signal?: AbortSignal): Promise<{ healthy: boolean; detail?: string }>;
}

export interface VadProvider extends Provider {
  readonly capabilities: ProviderCapabilities & { readonly kind: "vad" };
  detect(audio: AsyncIterable<AudioChunk>, context: ProviderOperationContext): AsyncIterable<VadEvent>;
}

export interface AsrProvider extends Provider {
  readonly capabilities: ProviderCapabilities & { readonly kind: "asr" };
  transcribe(audio: AsyncIterable<AudioChunk>, locale: Locale, context: ProviderOperationContext): AsyncIterable<TranscriptEvent>;
}

export interface AdmissionProvider extends Provider {
  readonly capabilities: ProviderCapabilities & { readonly kind: "admission" };
  evaluate(transcript: TranscriptEvent, context: ProviderOperationContext): Promise<AdmissionDecision>;
}

export interface LlmProvider extends Provider {
  readonly capabilities: ProviderCapabilities & { readonly kind: "llm" };
  generate(request: LlmRequest, context: ProviderOperationContext): AsyncIterable<LlmEvent>;
}

export interface TtsProvider extends Provider {
  readonly capabilities: ProviderCapabilities & { readonly kind: "tts" };
  synthesize(request: TtsRequest, context: ProviderOperationContext): AsyncIterable<AudioChunk>;
}

export interface ToolProvider extends Provider {
  readonly capabilities: ProviderCapabilities & { readonly kind: "tool" };
  list(context: ProviderOperationContext): Promise<readonly ToolDefinition[]>;
  call(name: string, input: unknown, context: ProviderOperationContext): Promise<unknown>;
}

export type AnyProvider = VadProvider | AsrProvider | AdmissionProvider | LlmProvider | TtsProvider | ToolProvider;
