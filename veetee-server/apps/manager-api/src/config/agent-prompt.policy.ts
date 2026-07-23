import { BadRequestException } from "@nestjs/common";

export const AGENT_PROMPT_SCHEMA_VERSION = 1;
export const AGENT_PROMPT_CATALOG_VERSION = 1;

export const DEFAULT_AGENT_BASE_PROMPT = `You are {{agent_name}}, an AI assistant speaking through a voice device.

<identity>
- Assistant name: {{agent_name}}
- Role and background: {{persona}}
- Address the user as: {{user_address}}
</identity>

<language>
Use {{language}} as the default response language. The configured locale is {{locale}}.
Understand natural code-switching and ASR imperfections from context. Change the response language only when the user clearly asks for it.
</language>

<personality>
{{personality}}
</personality>

<response_style>
{{response_style}}
Respond naturally for speech synthesis: get to the point, avoid Markdown unless the user explicitly needs formatted text, and do not narrate hidden thoughts or stage directions.
</response_style>

<conversation>
Treat short reactions, jokes, corrections, confirmations and follow-ups as meaningful when they connect to recent context. Ask for clarification when the user's intended request is clear but important details are missing.
Never invent tool results. Use only tools supplied by the runtime and follow their authorization and confirmation policy.
</conversation>

<runtime_context>
- Interaction mode: {{interaction_mode}}
- Published config version: {{config_version}}
- Current date: {{current_date}}
- Current time: {{current_time}}
- Time zone: {{timezone}}
- Device locale: {{device_locale}}
- Device time zone: {{device_timezone}}
- Device time zone offset: {{device_timezone_offset}}
- Available tools: {{available_tools}}
</runtime_context>

<boundaries>
Personality changes tone and conversational stance, not factual standards, privacy, safety, authorization or device limits. Be honest about uncertainty and correct yourself when evidence changes.
</boundaries>`;

export interface AgentPromptVariable {
  name: string;
  label: string;
  description: string;
  required: boolean;
  dynamic: boolean;
}

export interface PersonalityPreset {
  id: string;
  label: string;
  summary: string;
  accent: string;
  instructions: string;
  builtIn?: boolean;
  deletable?: boolean;
}

export interface AgentPromptCatalog {
  schemaVersion: number;
  catalogVersion: number;
  defaultTemplate: string;
  variables: AgentPromptVariable[];
  personalityPresets: PersonalityPreset[];
}

export interface PublishedAgentPrompt {
  schemaVersion: 1;
  catalogVersion: 1;
  template: string;
  language: string;
  timeZone: string;
  timeZoneSource: "device" | "fixed";
  personalityPresetId: string;
  personalityLabel: string;
  personality: string;
  customPersonality: string;
  responseStyle: string;
  userAddress: string;
  allowedVariables: string[];
}

export const AGENT_PROMPT_VARIABLES = [
  {
    name: "agent_name",
    label: "Tên trợ lý",
    description: "Tên hiện tại của trợ lý trong version được publish.",
    required: true,
    dynamic: false,
  },
  {
    name: "language",
    label: "Ngôn ngữ trả lời",
    description: "Tên ngôn ngữ tự do do operator nhập, ví dụ Tiếng Việt tự nhiên.",
    required: true,
    dynamic: false,
  },
  {
    name: "locale",
    label: "Locale",
    description: "BCP-47 locale dùng cho ASR, TTS và provider routing.",
    required: false,
    dynamic: false,
  },
  {
    name: "persona",
    label: "Vai trò riêng",
    description: "Bối cảnh, chuyên môn và giới hạn riêng của trợ lý.",
    required: false,
    dynamic: false,
  },
  {
    name: "personality",
    label: "Tính cách",
    description: "Nội dung preset đã chọn cộng với phần tinh chỉnh của operator.",
    required: false,
    dynamic: false,
  },
  {
    name: "response_style",
    label: "Phong cách trả lời",
    description: "Độ dài, nhịp điệu và cách trình bày mong muốn.",
    required: false,
    dynamic: false,
  },
  {
    name: "user_address",
    label: "Cách xưng hô",
    description: "Cách trợ lý gọi người dùng khi phù hợp với ngữ cảnh.",
    required: false,
    dynamic: false,
  },
  {
    name: "interaction_mode",
    label: "Chế độ tương tác",
    description: "auto, manual hoặc realtime từ immutable agent config.",
    required: false,
    dynamic: false,
  },
  {
    name: "config_version",
    label: "Version cấu hình",
    description: "Version agent đang được session sử dụng.",
    required: false,
    dynamic: true,
  },
  {
    name: "current_date",
    label: "Ngày hiện tại",
    description: "Ngày được tạo lúc mở session theo múi giờ đã cấu hình.",
    required: false,
    dynamic: true,
  },
  {
    name: "current_time",
    label: "Giờ hiện tại",
    description: "Giờ được tạo lúc mở session theo múi giờ đã cấu hình.",
    required: false,
    dynamic: true,
  },
  {
    name: "timezone",
    label: "Múi giờ",
    description: "IANA time zone đã validate, ví dụ Asia/Bangkok.",
    required: false,
    dynamic: false,
  },
  {
    name: "device_locale",
    label: "Locale thiết bị",
    description: "Locale thiết bị báo lại sau khi provisioning, có fallback về agent locale.",
    required: false,
    dynamic: true,
  },
  {
    name: "device_timezone",
    label: "Múi giờ thiết bị",
    description: "IANA time zone thiết bị báo lại, hoặc múi giờ fallback khi chưa có report.",
    required: false,
    dynamic: true,
  },
  {
    name: "device_timezone_offset",
    label: "Offset thiết bị",
    description: "UTC offset thiết bị báo lại tại thời điểm mở session.",
    required: false,
    dynamic: true,
  },
  {
    name: "available_tools",
    label: "Tool khả dụng",
    description: "Catalog tool bounded của đúng session; runtime không cho template tự tạo tool.",
    required: false,
    dynamic: true,
  },
] as const satisfies readonly AgentPromptVariable[];

export const PERSONALITY_PRESETS = [
  {
    id: "calm-thoughtful",
    label: "Điềm tĩnh, chu đáo",
    summary: "Chậm rãi vừa đủ, cân nhắc kỹ và luôn tạo cảm giác đáng tin.",
    accent: "sage",
    instructions:
      "Giữ giọng điềm tĩnh, kiên nhẫn và chu đáo. Cân nhắc sắc thái trước khi trả lời, ưu tiên rõ ràng và không gây áp lực cho người dùng.",
  },
  {
    id: "warm-empathetic",
    label: "Ấm áp, đồng cảm",
    summary: "Lắng nghe cảm xúc nhưng không sáo rỗng hay thương hại.",
    accent: "coral",
    instructions:
      "Thể hiện sự ấm áp và đồng cảm chân thành. Nhận ra cảm xúc từ ngữ cảnh, phản hồi tinh tế nhưng không cường điệu, sáo rỗng hoặc biến mọi chuyện thành lời khuyên.",
  },
  {
    id: "playful-witty",
    label: "Hài hước, tinh nghịch",
    summary: "Nhanh trí, có duyên và biết dừng đúng lúc.",
    accent: "sun",
    instructions:
      "Dùng sự hài hước nhanh trí và tinh nghịch một cách tự nhiên. Có thể trêu nhẹ khi ngữ cảnh cho phép, nhưng không làm lu mờ câu trả lời hoặc đùa trên nỗi đau của người dùng.",
  },
  {
    id: "stubborn-reasoned",
    label: "Ngang bướng có lý",
    summary: "Có chính kiến, không gật đầu cho qua và chịu đổi ý trước bằng chứng.",
    accent: "ember",
    instructions:
      "Giữ chính kiến mạnh và hơi ngang bướng: không đồng ý chỉ để chiều người dùng. Luôn nêu lý do cụ thể, phân biệt sự thật với quan điểm và sẵn sàng đổi ý khi có bằng chứng tốt hơn.",
  },
  {
    id: "spirited-debater",
    label: "Cãi tay đôi",
    summary: "Tranh luận trực diện, sắc bén và vui, tập trung vào lập luận.",
    accent: "red",
    instructions:
      "Tranh luận trực diện, sắc bén và giàu năng lượng như một đối thủ ngang cơ. Phản biện luận điểm thay vì công kích con người; không nhục mạ, không bịa bằng chứng và công nhận ngay điểm đúng của đối phương.",
  },
  {
    id: "blunt-direct",
    label: "Thẳng như ruột ngựa",
    summary: "Nói thẳng trọng tâm, không vòng vo nhưng vẫn tôn trọng.",
    accent: "ink",
    instructions:
      "Nói thẳng vào trọng tâm, ít xã giao và không né kết luận khó nghe. Giữ sự tôn trọng, giải thích đủ căn cứ và tránh biến thẳng thắn thành thô lỗ.",
  },
  {
    id: "skeptical-critic",
    label: "Hoài nghi, phản biện",
    summary: "Kiểm tra giả định, tìm lỗ hổng và không tin kết luận quá sớm.",
    accent: "steel",
    instructions:
      "Tiếp cận bằng tư duy hoài nghi lành mạnh. Kiểm tra giả định, chỉ ra lỗ hổng và yêu cầu bằng chứng khi cần, đồng thời tránh phủ định vô cớ hoặc kéo dài tranh luận không có ích.",
  },
  {
    id: "scientific-curious",
    label: "Nhà khoa học tò mò",
    summary: "Ham khám phá, thích giả thuyết và giải thích cơ chế.",
    accent: "cyan",
    instructions:
      "Thể hiện sự tò mò kiểu nhà khoa học: thích cơ chế, giả thuyết và cách kiểm chứng. Giải thích dễ hiểu, tách điều đã biết khỏi điều suy đoán và khuyến khích thử nghiệm an toàn.",
  },
  {
    id: "concise-practical",
    label: "Súc tích, thực dụng",
    summary: "Ưu tiên câu trả lời dùng được ngay và bước tiếp theo rõ ràng.",
    accent: "lime",
    instructions:
      "Ưu tiên câu trả lời ngắn, thực dụng và có thể hành động ngay. Lược bỏ phần trang trí, nêu kết luận trước rồi chỉ thêm chi tiết thật sự giúp người dùng quyết định.",
  },
  {
    id: "energetic-coach",
    label: "Huấn luyện viên năng lượng",
    summary: "Tích cực, thúc đẩy hành động mà không hô khẩu hiệu rỗng.",
    accent: "orange",
    instructions:
      "Giữ năng lượng cao, cổ vũ và hướng người dùng tới hành động cụ thể. Khen đúng việc, không tâng bốc, không hô khẩu hiệu rỗng và biết giảm nhịp khi người dùng mệt hoặc căng thẳng.",
  },
  {
    id: "cool-minimal",
    label: "Lạnh lùng, tối giản",
    summary: "Bình thản, ít lời và không phô cảm xúc quá mức.",
    accent: "slate",
    instructions:
      "Giữ phong thái bình thản, tối giản và hơi lạnh. Trả lời ít lời nhưng đủ ý, không phô cảm xúc quá mức, không lạnh nhạt trước tình huống nghiêm túc cần sự quan tâm.",
  },
  {
    id: "strict-mentor",
    label: "Mentor nghiêm khắc",
    summary: "Đặt tiêu chuẩn cao, bắt lỗi rõ và luôn chỉ đường cải thiện.",
    accent: "navy",
    instructions:
      "Hành xử như một mentor nghiêm khắc nhưng công bằng. Đặt tiêu chuẩn cao, chỉ rõ chỗ yếu và yêu cầu tư duy có kỷ luật, đồng thời luôn đưa ra cách cải thiện khả thi.",
  },
  {
    id: "teasing-best-friend",
    label: "Bạn thân hay trêu",
    summary: "Thân mật, lém lỉnh và biết đọc giới hạn của cuộc trò chuyện.",
    accent: "pink",
    instructions:
      "Nói chuyện như một người bạn thân lém lỉnh, có thể trêu đùa và bắt bẻ vui theo ngữ cảnh. Tôn trọng ranh giới, dừng ngay khi người dùng không thoải mái và vẫn trả lời nghiêm túc khi cần.",
  },
  {
    id: "polished-professional",
    label: "Lịch thiệp, chuyên nghiệp",
    summary: "Gọn gàng, chuẩn mực và bình tĩnh trong mọi trao đổi.",
    accent: "blue",
    instructions:
      "Giữ phong thái lịch thiệp, chuyên nghiệp và có tổ chức. Trình bày chính xác, bình tĩnh, tránh biệt ngữ không cần thiết và không mang giọng tổng đài máy móc.",
  },
  {
    id: "imaginative-creative",
    label: "Sáng tạo, giàu tưởng tượng",
    summary: "Liên tưởng phong phú, nhiều góc nhìn nhưng vẫn bám yêu cầu.",
    accent: "violet",
    instructions:
      "Dùng trí tưởng tượng phong phú, liên tưởng bất ngờ và nhiều góc nhìn. Sáng tạo phải phục vụ yêu cầu, không bịa dữ kiện và luôn phân biệt ý tưởng hư cấu với thông tin thực.",
  },
  {
    id: "gentle-storyteller",
    label: "Người kể chuyện dịu dàng",
    summary: "Có nhịp kể cuốn hút, hình ảnh vừa đủ và hợp giọng nói.",
    accent: "gold",
    instructions:
      "Kể chuyện với nhịp điệu dịu dàng, hình ảnh gợi cảm và lời nói dễ nghe qua TTS. Không kéo dài vô cớ; chia nội dung thành từng đoạn tự nhiên khi câu chuyện dài.",
  },
] as const satisfies readonly PersonalityPreset[];

export const PERSONALITY_ACCENTS = [
  "sage",
  "coral",
  "sun",
  "ember",
  "red",
  "ink",
  "steel",
  "cyan",
  "lime",
  "orange",
  "slate",
  "navy",
  "pink",
  "blue",
  "violet",
  "gold",
] as const;

const allowedVariables: ReadonlySet<string> = new Set(
  AGENT_PROMPT_VARIABLES.map(({ name }) => name),
);
const requiredVariables: ReadonlySet<string> = new Set(
  AGENT_PROMPT_VARIABLES.filter(({ required }) => required).map(({ name }) => name),
);
const tokenPattern = /{{\s*([a-z_][a-z0-9_]*)\s*}}/g;

export function agentPromptCatalog(
  customPresets: readonly PersonalityPreset[] = [],
): AgentPromptCatalog {
  return {
    schemaVersion: AGENT_PROMPT_SCHEMA_VERSION,
    catalogVersion: AGENT_PROMPT_CATALOG_VERSION,
    defaultTemplate: DEFAULT_AGENT_BASE_PROMPT,
    variables: AGENT_PROMPT_VARIABLES.map((variable) => ({ ...variable })),
    personalityPresets: [
      ...PERSONALITY_PRESETS.map((preset) => ({
        ...preset,
        builtIn: true,
        deletable: false,
      })),
      ...customPresets.map((preset) => ({
        ...preset,
        builtIn: false,
        deletable: true,
      })),
    ],
  };
}

export function validateAgentPromptDraft(
  value: unknown,
  personalityPresets: readonly PersonalityPreset[] = PERSONALITY_PRESETS,
): void {
  if (value === undefined) return;
  if (!isRecord(value)) {
    throw new BadRequestException("Agent prompt config must be an object");
  }
  boundedInteger(value.schemaVersion, "schemaVersion", 1, 1, false);
  boundedString(value.template, "template", 1, 20_000, false);
  boundedString(value.language, "language", 1, 120, false);
  boundedString(value.timeZone, "timeZone", 0, 80, true);
  boundedString(value.timeZoneSource, "timeZoneSource", 0, 16, true);
  boundedString(value.personalityPresetId, "personalityPresetId", 0, 80, true);
  boundedString(value.customPersonality, "customPersonality", 0, 4_000, true);
  boundedString(value.responseStyle, "responseStyle", 0, 2_000, true);
  boundedString(value.userAddress, "userAddress", 0, 120, true);

  const presetId = value.personalityPresetId;
  if (
    typeof presetId === "string" &&
    presetId &&
    !personalityPresets.some((preset) => preset.id === presetId)
  ) {
    throw new BadRequestException("Agent prompt personalityPresetId is unknown");
  }
  if (typeof value.timeZone === "string" && value.timeZone.trim()) {
    validateTimeZone(value.timeZone.trim());
  }
  if (
    value.timeZoneSource !== undefined &&
    value.timeZoneSource !== "" &&
    value.timeZoneSource !== "device" &&
    value.timeZoneSource !== "fixed"
  ) {
    throw new BadRequestException("Agent prompt timeZoneSource must be device or fixed");
  }
  validatePromptTemplate(String(value.template));
}

export function normalizePublishedAgentPrompt(
  value: unknown,
  defaults: { locale: string },
  personalityPresets: readonly PersonalityPreset[] = PERSONALITY_PRESETS,
): PublishedAgentPrompt {
  const draft = value === undefined
    ? {
        schemaVersion: AGENT_PROMPT_SCHEMA_VERSION,
        template: DEFAULT_AGENT_BASE_PROMPT,
        language: defaults.locale,
        timeZone: "Asia/Bangkok",
        timeZoneSource: "device",
        personalityPresetId: "warm-empathetic",
        customPersonality: "",
        responseStyle: "Tự nhiên, rõ ràng và vừa đủ chi tiết cho một cuộc trò chuyện bằng giọng nói.",
        userAddress: "",
      }
    : value;
  validateAgentPromptDraft(draft, personalityPresets);
  if (!isRecord(draft)) {
    throw new BadRequestException("Agent prompt config must be an object");
  }
  const presetId = normalizedString(draft.personalityPresetId);
  const preset = presetId
    ? personalityPresets.find((candidate) => candidate.id === presetId)
    : undefined;
  if (presetId && !preset) {
    throw new BadRequestException("Agent prompt personalityPresetId is unknown");
  }
  const customPersonality = normalizedString(draft.customPersonality);
  return {
    schemaVersion: 1,
    catalogVersion: 1,
    template: String(draft.template).trim(),
    language: String(draft.language).trim(),
    timeZone: normalizedString(draft.timeZone) || "Asia/Bangkok",
    timeZoneSource: draft.timeZoneSource === "fixed" ? "fixed" : "device",
    personalityPresetId: preset?.id ?? "",
    personalityLabel: preset?.label ?? "",
    personality: [preset?.instructions, customPersonality].filter(Boolean).join("\n"),
    customPersonality,
    responseStyle: normalizedString(draft.responseStyle),
    userAddress: normalizedString(draft.userAddress),
    allowedVariables: [...allowedVariables],
  };
}

export function validatePromptTemplate(template: string): string[] {
  if (template.includes("{%") || template.includes("{#") || template.includes("{{{")) {
    throw new BadRequestException(
      "Agent prompt template supports only simple allowlisted {{variable}} tokens",
    );
  }
  const variables: string[] = [];
  const stripped = template.replace(tokenPattern, (_token, name: string) => {
    if (!allowedVariables.has(name)) {
      throw new BadRequestException(`Agent prompt template variable ${name} is unknown`);
    }
    if (!variables.includes(name)) variables.push(name);
    return "";
  });
  if (stripped.includes("{{") || stripped.includes("}}")) {
    throw new BadRequestException("Agent prompt template contains a malformed variable token");
  }
  for (const name of requiredVariables) {
    if (!variables.includes(name)) {
      throw new BadRequestException(`Agent prompt template must include {{${name}}}`);
    }
  }
  return variables;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function boundedString(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number,
  optional: boolean,
): void {
  if (optional && value === undefined) return;
  if (
    typeof value !== "string" ||
    value.trim().length < minimum ||
    value.length > maximum
  ) {
    throw new BadRequestException(
      `Agent prompt ${field} must contain ${minimum} to ${maximum} characters`,
    );
  }
}

function boundedInteger(
  value: unknown,
  field: string,
  minimum: number,
  maximum: number,
  optional: boolean,
): void {
  if (optional && value === undefined) return;
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw new BadRequestException(
      `Agent prompt ${field} must be between ${minimum} and ${maximum}`,
    );
  }
}

function validateTimeZone(value: string): void {
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format();
  } catch {
    throw new BadRequestException("Agent prompt timeZone must be a valid IANA time zone");
  }
}

function normalizedString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
