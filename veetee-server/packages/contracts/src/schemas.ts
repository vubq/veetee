const id = { type: "string", minLength: 1 } as const;
const sessionId = { type: "string", minLength: 1, maxLength: 64 } as const;
const reasonCode = { type: "string", minLength: 1, maxLength: 64 } as const;
const nonNegativeInteger = { type: "integer", minimum: 0 } as const;
const positiveNumber = { type: "number", exclusiveMinimum: 0 } as const;
const sha256 = { type: "string", pattern: "^[a-f0-9]{64}$" } as const;

export const deviceCapabilitySchema = {
  $id: "https://schemas.veetee.local/artifacts/device-capability-v1.json",
  type: "object",
  additionalProperties: false,
  required: [
    "board",
    "firmware_version",
    "resource_abi",
    "runtimes",
    "free_resource_slot_bytes",
    "psram_bytes",
    "hot_reload",
  ],
  properties: {
    board: id,
    firmware_version: id,
    resource_abi: nonNegativeInteger,
    runtimes: {
      type: "object",
      minProperties: 1,
      additionalProperties: {
        type: "array",
        minItems: 1,
        uniqueItems: true,
        items: nonNegativeInteger,
      },
    },
    free_resource_slot_bytes: nonNegativeInteger,
    psram_bytes: nonNegativeInteger,
    hot_reload: {
      type: "array",
      uniqueItems: true,
      items: { enum: ["model_pack", "display_assets", "audio_assets"] },
    },
  },
} as const;

export const resourceManifestSchema = {
  $id: "https://schemas.veetee.local/artifacts/resource-manifest-v1.json",
  type: "object",
  additionalProperties: false,
  required: [
    "manifest_version",
    "bundle_id",
    "kind",
    "version",
    "channel",
    "target",
    "compatibility",
    "payload",
    "apply",
    "members",
    "created_at",
    "signature",
  ],
  properties: {
    manifest_version: { const: 1 },
    bundle_id: id,
    kind: { const: "resource_bundle" },
    version: id,
    channel: { enum: ["development", "canary", "stable"] },
    target: {
      type: "object",
      additionalProperties: false,
      required: ["board", "chip", "flash_bytes", "psram_bytes"],
      properties: {
        board: id,
        chip: { const: "esp32s3" },
        flash_bytes: nonNegativeInteger,
        psram_bytes: nonNegativeInteger,
      },
    },
    compatibility: {
      type: "object",
      additionalProperties: false,
      required: ["min_firmware", "max_firmware_exclusive", "resource_abi"],
      properties: {
        min_firmware: id,
        max_firmware_exclusive: id,
        resource_abi: nonNegativeInteger,
      },
    },
    payload: {
      type: "object",
      additionalProperties: false,
      required: ["url", "size", "sha256", "content_type"],
      properties: {
        url: { type: "string", format: "uri" },
        size: nonNegativeInteger,
        sha256,
        content_type: id,
      },
    },
    apply: {
      type: "object",
      additionalProperties: false,
      required: ["mode", "requires_reboot", "rollback_allowed"],
      properties: {
        mode: { enum: ["immediate", "when_standby", "on_reboot"] },
        requires_reboot: { type: "boolean" },
        rollback_allowed: { type: "boolean" },
      },
    },
    members: {
      type: "array",
      minItems: 1,
      items: {
        type: "object",
        additionalProperties: true,
        required: ["name", "kind", "runtime", "runtime_abi", "format_version", "sha256", "bytes"],
        properties: {
          name: id,
          kind: { enum: ["model_pack", "display_assets", "audio_assets"] },
          runtime: id,
          runtime_abi: nonNegativeInteger,
          format_version: nonNegativeInteger,
          sha256,
          bytes: nonNegativeInteger,
        },
      },
    },
    created_at: { type: "string", format: "date-time" },
    signature: {
      type: "object",
      additionalProperties: false,
      required: ["algorithm", "key_id", "security_epoch", "value"],
      properties: {
        algorithm: { const: "ed25519" },
        key_id: id,
        security_epoch: nonNegativeInteger,
        value: id,
      },
    },
  },
} as const;

export const signedManifestVectorSchema = {
  $id: "https://schemas.veetee.local/artifacts/signed-manifest-vector-v1.json",
  type: "object",
  additionalProperties: false,
  required: [
    "algorithm",
    "canonicalization",
    "public_key_spki_base64",
    "document",
    "canonical_payload",
    "signature_base64",
  ],
  properties: {
    algorithm: { const: "ed25519" },
    canonicalization: { const: "RFC8785-JCS" },
    public_key_spki_base64: { type: "string", minLength: 1 },
    document: { type: "object" },
    canonical_payload: { type: "string", minLength: 1 },
    signature_base64: { type: "string", minLength: 1 },
  },
} as const;

export const conversationPolicySchema = {
  $id: "https://schemas.veetee.local/config/agent-conversation-policy-v1.json",
  type: "object",
  additionalProperties: false,
  required: [
    "schema_version",
    "default_locale",
    "interaction_mode",
    "conversation_engine",
    "wake",
    "input_admission",
    "timeouts",
    "mcp",
  ],
  properties: {
    schema_version: { const: 1 },
    default_locale: { type: "string", pattern: "^[a-z]{2,3}(?:-[A-Z]{2})?$" },
    interaction_mode: { enum: ["auto", "manual", "realtime"] },
    conversation_engine: { enum: ["cascade", "realtime"] },
    wake: {
      type: "object",
      additionalProperties: false,
      required: ["activation_detector_profile_id", "interrupt_detector_profile_id"],
      properties: {
        activation_detector_profile_id: id,
        interrupt_detector_profile_id: id,
      },
    },
    input_admission: {
      type: "object",
      additionalProperties: false,
      required: ["policy", "on_unclear", "target_speaker", "max_clarification_attempts"],
      properties: {
        policy: id,
        on_unclear: { enum: ["ignore", "ask_once"] },
        target_speaker: { enum: ["disabled", "optional", "required"] },
        max_clarification_attempts: nonNegativeInteger,
      },
    },
    timeouts: {
      type: "object",
      additionalProperties: false,
      required: [
        "first_input_seconds",
        "between_turns_seconds",
        "closing_grace_seconds",
        "max_utterance_seconds",
        "max_session_seconds",
        "admission_seconds",
        "asr_seconds",
        "planner_seconds",
        "llm_first_token_seconds",
        "tts_first_audio_seconds",
        "mcp_seconds",
        "total_turn_seconds",
      ],
      properties: {
        first_input_seconds: positiveNumber,
        between_turns_seconds: positiveNumber,
        closing_grace_seconds: positiveNumber,
        max_utterance_seconds: positiveNumber,
        max_session_seconds: positiveNumber,
        admission_seconds: positiveNumber,
        asr_seconds: positiveNumber,
        planner_seconds: positiveNumber,
        llm_first_token_seconds: positiveNumber,
        tts_first_audio_seconds: positiveNumber,
        mcp_seconds: positiveNumber,
        total_turn_seconds: positiveNumber,
      },
    },
    mcp: {
      type: "object",
      additionalProperties: false,
      required: ["enabled", "require_confirmation_for_user_tools"],
      properties: {
        enabled: { type: "boolean" },
        require_confirmation_for_user_tools: { type: "boolean" },
      },
    },
  },
} as const;

export const providerBaselineSchema = {
  $id: "https://schemas.veetee.local/config/provider-baseline-v1.json",
  type: "object",
  additionalProperties: false,
  required: ["schema_version", "locale", "vad", "asr", "llm", "tts", "privacy"],
  properties: {
    schema_version: { const: 1 },
    locale: id,
    vad: {
      type: "object",
      required: ["provider_id", "model_ref", "runtime", "endpointing"],
      additionalProperties: true,
      properties: {
        provider_id: id,
        model_ref: id,
        runtime: id,
        endpointing: { type: "boolean" },
      },
    },
    asr: {
      type: "object",
      additionalProperties: false,
      required: ["primary", "fallback"],
      properties: {
        primary: {
          type: "object",
          required: ["provider_id", "model_ref", "streaming", "quality_gate"],
          additionalProperties: true,
          properties: {
            provider_id: id,
            model_ref: id,
            streaming: { type: "boolean" },
            quality_gate: id,
          },
        },
        fallback: {
          type: "object",
          required: ["provider_id", "model_ref", "streaming", "trigger", "same_turn_deadline"],
          additionalProperties: true,
          properties: {
            provider_id: id,
            model_ref: id,
            streaming: { type: "boolean" },
            trigger: id,
            same_turn_deadline: { type: "boolean" },
          },
        },
      },
    },
    llm: {
      type: "object",
      required: ["provider_id", "api_style", "base_url_env", "model_ref", "streaming", "structured_output", "tool_calling", "reasoning_effort", "auth_mode"],
      additionalProperties: true,
      properties: {
        provider_id: id,
        api_style: id,
        base_url_env: id,
        model_ref: id,
        streaming: { type: "boolean" },
        structured_output: { type: "boolean" },
        tool_calling: { type: "boolean" },
        reasoning_effort: id,
        auth_mode: id,
      },
    },
    tts: {
      type: "object",
      required: ["provider_id", "model_ref", "streaming", "audio_format", "locale"],
      additionalProperties: true,
      properties: {
        provider_id: id,
        model_ref: id,
        streaming: { anyOf: [{ type: "boolean" }, { type: "string" }] },
        audio_format: id,
        locale: id,
      },
    },
    privacy: {
      type: "object",
      additionalProperties: false,
      required: ["raw_audio_storage", "external_provider_fallback"],
      properties: {
        raw_audio_storage: { type: "boolean" },
        external_provider_fallback: { type: "boolean" },
      },
    },
  },
} as const;

export const mcpEnvelopeSchema = {
  $id: "https://schemas.veetee.local/mcp/envelope-v1.json",
  type: "object",
  additionalProperties: false,
  required: ["session_id", "type", "payload"],
  properties: {
    session_id: sessionId,
    type: { const: "mcp" },
    payload: {
      type: "object",
      additionalProperties: false,
      required: ["jsonrpc", "id", "method", "params"],
      properties: {
        jsonrpc: { const: "2.0" },
        id: { anyOf: [{ type: "string" }, { type: "integer" }] },
        method: id,
        params: { type: "object" },
      },
    },
  },
} as const;

export const otaBootstrapSchema = {
  $id: "https://schemas.veetee.local/ota/bootstrap-v1.json",
  type: "object",
  additionalProperties: false,
  required: ["server_time", "websocket", "firmware"],
  properties: {
    server_time: {
      type: "object",
      additionalProperties: false,
      required: ["timestamp", "timezone_offset"],
      properties: { timestamp: nonNegativeInteger, timezone_offset: { type: "integer", minimum: -840, maximum: 840 } },
    },
    activation: {
      type: "object",
      additionalProperties: false,
      required: ["code", "message", "challenge"],
      properties: {
        code: { type: "string", pattern: "^[0-9]{6}$" },
        message: id,
        challenge: id,
        expires_at: { type: "string", format: "date-time" },
        timeout_ms: { type: "integer", minimum: 1000, maximum: 3_600_000 },
      },
    },
    websocket: {
      type: "object",
      additionalProperties: false,
      required: ["url", "token"],
      properties: { url: { type: "string", format: "uri" }, token: { type: "string" } },
    },
    firmware: {
      type: "object",
      additionalProperties: false,
      required: ["version", "url"],
      properties: { version: id, url: { type: "string" } },
    },
    config: {
      type: "object",
      additionalProperties: false,
      required: ["version", "etag", "url"],
      properties: { version: nonNegativeInteger, etag: id, url: { type: "string", format: "uri" } },
    },
    resources: {
      type: "object",
      additionalProperties: false,
      required: ["version", "manifest_url"],
      properties: { version: id, manifest_url: { type: "string", format: "uri" } },
    },
  },
} as const;

const sessionEvent = {
  type: "object",
  required: ["session_id", "type"],
  properties: { session_id: sessionId, type: id },
} as const;

export const webSocketEventSchema = {
  $id: "https://schemas.veetee.local/ws/control-event-v1.json",
  oneOf: [
    {
      type: "object",
      additionalProperties: false,
      required: ["type", "version", "features", "transport", "audio_params"],
      properties: {
        type: { const: "hello" },
        version: { const: 1 },
        features: {
          type: "object",
          additionalProperties: false,
          required: ["mcp", "aec", "glyph_push"],
          properties: { mcp: { type: "boolean" }, aec: { type: "boolean" }, glyph_push: { type: "boolean" } },
        },
        transport: { const: "websocket" },
        audio_params: { $ref: "#/$defs/audio_params" },
      },
    },
    {
      type: "object",
      additionalProperties: false,
      required: ["type", "transport", "session_id", "audio_params"],
      properties: {
        type: { const: "hello" },
        transport: { const: "websocket" },
        session_id: sessionId,
        audio_params: { $ref: "#/$defs/audio_params" },
      },
    },
    {
      ...sessionEvent,
      additionalProperties: false,
      required: ["session_id", "type", "state"],
      properties: {
        session_id: sessionId,
        type: { const: "listen" },
        state: { enum: ["start", "stop", "detect"] },
        mode: { enum: ["auto", "manual", "realtime"] },
        source: { enum: ["button", "wake_word"] },
        text: { type: "string" },
        reason: reasonCode,
      },
    },
    {
      ...sessionEvent,
      additionalProperties: false,
      required: ["session_id", "type"],
      properties: {
        session_id: sessionId,
        type: { const: "abort" },
        reason: reasonCode,
        source: { enum: ["button", "wake_word", "interrupt_profile", "server"] },
      },
    },
    {
      ...sessionEvent,
      additionalProperties: false,
      required: ["session_id", "type", "command"],
      properties: {
        session_id: sessionId,
        type: { const: "system" },
        command: { enum: ["assistant_sleep", "config_changed"] },
        reason: reasonCode,
        config_version: nonNegativeInteger,
        resource_version: id,
      },
    },
  ],
  $defs: {
    audio_params: {
      type: "object",
      additionalProperties: false,
      required: ["format", "sample_rate", "channels", "frame_duration"],
      properties: {
        format: { const: "opus" },
        sample_rate: { type: "integer", enum: [16000, 24000, 48000] },
        channels: { const: 1 },
        frame_duration: { type: "integer", minimum: 10, maximum: 120 },
      },
    },
  },
} as const;

export const schemas = [
  deviceCapabilitySchema,
  resourceManifestSchema,
  signedManifestVectorSchema,
  conversationPolicySchema,
  providerBaselineSchema,
  mcpEnvelopeSchema,
  otaBootstrapSchema,
  webSocketEventSchema,
] as const;
