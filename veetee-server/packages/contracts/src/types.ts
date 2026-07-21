export type Locale = string;
export type InteractionMode = "auto" | "manual" | "realtime";
export type WakeSource = "button" | "wake_word";

export interface AudioParameters {
  format: "opus";
  sample_rate: number;
  channels: 1;
  frame_duration: number;
}

export interface DeviceHello {
  type: "hello";
  version: 1;
  features: {
    mcp: boolean;
    aec: boolean;
    glyph_push: boolean;
  };
  transport: "websocket";
  audio_params: AudioParameters;
}

export interface ServerHello {
  type: "hello";
  transport: "websocket";
  session_id: string;
  audio_params: AudioParameters;
}

export interface ListenEvent {
  session_id: string;
  type: "listen";
  state: "start" | "stop" | "detect";
  mode?: InteractionMode;
  source?: WakeSource;
  text?: string;
  reason?: string;
}

export interface AbortEvent {
  session_id: string;
  type: "abort";
  reason: string;
  source?: "button" | "wake_word" | "interrupt_profile" | "server";
}

export interface SttEvent {
  session_id: string;
  type: "stt";
  text: string;
}

export interface TtsEvent {
  session_id: string;
  type: "tts";
  state: "start" | "sentence_start" | "stop";
  text?: string;
}

export interface LlmEvent {
  session_id: string;
  type: "llm";
  emotion: string;
  text?: string;
}

export interface SystemEvent {
  session_id: string;
  type: "system";
  command: "assistant_sleep" | "config_changed";
  reason?: string;
  config_version?: number;
  resource_version?: string;
}

export type WebSocketControlEvent =
  | DeviceHello
  | ServerHello
  | ListenEvent
  | AbortEvent
  | SttEvent
  | TtsEvent
  | LlmEvent
  | SystemEvent;

export type AdmissionDisposition =
  | "accepted"
  | "non_actionable"
  | "not_addressed"
  | "unclear"
  | "interrupt";

export interface AdmissionDecision {
  disposition: AdmissionDisposition;
  confidence: number;
  locale: Locale;
  rationale_code?: string;
}

export interface ConversationTimeouts {
  first_input_seconds: number;
  between_turns_seconds: number;
  closing_grace_seconds: number;
  max_utterance_seconds: number;
  max_session_seconds: number;
  admission_seconds: number;
  asr_seconds: number;
  planner_seconds: number;
  llm_first_token_seconds: number;
  tts_first_audio_seconds: number;
  mcp_seconds: number;
  total_turn_seconds: number;
}

export interface ConversationPolicy {
  schema_version: 1;
  default_locale: Locale;
  interaction_mode: InteractionMode;
  conversation_engine: "cascade" | "realtime";
  wake: {
    activation_detector_profile_id: string;
    interrupt_detector_profile_id: string;
  };
  input_admission: {
    policy: string;
    on_unclear: "ignore" | "ask_once";
    target_speaker: "disabled" | "optional" | "required";
    max_clarification_attempts: number;
  };
  timeouts: ConversationTimeouts;
  mcp: {
    enabled: boolean;
    require_confirmation_for_user_tools: boolean;
  };
}

export interface JsonRpcRequest<TParams = unknown> {
  jsonrpc: "2.0";
  id: string | number;
  method: string;
  params?: TParams;
}

export interface McpEnvelope<TParams = unknown> {
  session_id: string;
  type: "mcp";
  payload: JsonRpcRequest<TParams>;
}

export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  audience: "regular" | "user_only";
}
