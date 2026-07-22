import type {
  Agent,
  Artifact,
  ConversationEvent,
  Device,
  McpTool,
  Principal,
  Provider,
  ResourceRollout,
  WakeProfile,
} from "./api/schemas";

const pageNames: Record<string, string> = {
  overview: "Tổng quan",
  devices: "Thiết bị",
  "device-ui": "Giao diện thiết bị",
  agents: "Trợ lý AI",
  providers: "Providers",
  lab: "Realtime Lab",
  mcp: "MCP tools",
  ota: "OTA & releases",
};

const deviceUiCopy = {
  vi: {
    idle: ["01", "SẴN SÀNG", "Chào bạn.", "Bấm nút hoặc nói “Hey VeeTee”"],
    listening: ["02", "ĐANG NGHE", "Bạn cứ nói.", "Nói tự nhiên · bấm để tạm dừng"],
    understanding: ["03", "ĐANG ĐÁNH GIÁ", "Tôi đang nghe rõ.", "Phân biệt lời nói và âm thanh nền"],
    thinking: ["04", "ĐANG XỬ LÝ", "Một chút nhé.", "AI có thể gọi công cụ khi cần"],
    speaking: ["05", "ĐANG TRẢ LỜI", "Tôi đang nói.", "Bấm nút để ngắt ngay"],
    pairing: ["07", "GHÉP THIẾT BỊ", "Nhập mã này.", "Mở Manager Web · mã chỉ dùng một lần"],
    pairingLost: ["08", "MẤT GHÉP NỐI", "Cần kết nối lại.", "Giữ nút 5 giây để bắt đầu phục hồi"],
  },
  en: {
    idle: ["01", "READY", "Hello there.", "Press the button or say “Hey VeeTee”"],
    listening: ["02", "LISTENING", "Go ahead.", "Speak naturally · press to pause"],
    understanding: ["03", "EVALUATING", "I can hear you.", "Separating speech from background audio"],
    thinking: ["04", "WORKING", "One moment.", "AI may call a tool when needed"],
    speaking: ["05", "RESPONDING", "I’m speaking.", "Press the button to interrupt"],
    pairing: ["07", "PAIR DEVICE", "Enter this code.", "Open Manager Web · single-use code"],
    pairingLost: ["08", "PAIRING LOST", "Let’s reconnect.", "Hold the button for 5 seconds to recover"],
  },
} as const;

const deviceUiThemeNames = {
  signal: "01 / Signal",
  monolith: "02 / Monolith",
  quiet: "03 / Quiet",
} as const;

export interface AgentDraftInput {
  id: string;
  name: string;
  defaultLocale: string;
  interactionMode: Agent["interactionMode"];
  persona: string;
  draftConfig: Record<string, unknown>;
}

export interface ProviderUpdateInput {
  adapter: string;
  model: string;
  baseUrl: string | null;
  enabled: boolean;
  priority: number;
  locales: string[];
  secretAction: "keep" | "rotate" | "clear";
  secret?: string;
}

interface PrototypeCallbacks {
  pair(code: string, name: string, agentId?: string): Promise<void>;
  testProvider(providerId: string): Promise<void>;
  updateProvider(providerId: string, input: ProviderUpdateInput): Promise<void>;
  publishAgent(input: AgentDraftInput): Promise<void>;
  callTool(
    deviceId: string,
    name: string,
    argumentsValue: Record<string, unknown>,
    confirmed: boolean,
  ): Promise<Record<string, unknown>>;
  registerArtifact(artifactId: string, license: string): Promise<void>;
  publishArtifact(id: string): Promise<void>;
  stageUiPack(file: File): Promise<Artifact>;
  rolloutUiPack(id: string): Promise<void>;
  createWakeProfile(input: WakeProfileDraftInput): Promise<void>;
  publishWakeProfile(id: string): Promise<void>;
  rolloutWakeProfile(id: string, deviceIds: string[]): Promise<void>;
  logout(): Promise<void>;
}

export interface WakeProfileDraftInput {
  artifactId: string;
  name: string;
  locale: string;
  channel: string;
  activationPhrase: string;
  activation: {
    detectorId: string;
    sensitivity: number;
    cooldownMs: number;
    allowedStates: string[];
  };
  interrupt: {
    detectorId: string;
    sensitivity: number;
    cooldownMs: number;
    allowedStates: string[];
  };
}

export interface PrototypeController {
  toast(message: string): void;
  closePairing(): void;
  destroy(): void;
}

export interface ManagerViewData {
  principal: Principal;
  devices: Device[];
  agents: Agent[];
  providers: Provider[];
  tools: McpTool[];
  conversationEvents: ConversationEvent[];
  artifacts: Artifact[];
  wakeProfiles: WakeProfile[];
  resourceRollouts: ResourceRollout[];
  activeDeviceId: string | undefined;
  toolsLive: boolean;
  apiHost: string;
  ready: boolean;
}

interface RenderSignatures {
  devices?: string;
  agents?: string;
  providers?: string;
  tools?: string;
}

const renderSignatures = new WeakMap<HTMLElement, RenderSignatures>();
const toolCatalogs = new WeakMap<
  HTMLElement,
  { tools: McpTool[]; activeDeviceId: string | undefined; live: boolean }
>();
const providerCatalogs = new WeakMap<HTMLElement, Provider[]>();

function query<T extends Element>(root: ParentNode, selector: string): T | null {
  return root.querySelector<T>(selector);
}

function queryAll<T extends Element>(root: ParentNode, selector: string): T[] {
  return [...root.querySelectorAll<T>(selector)];
}

function escapeHtml(value: unknown): string {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function conversationConfig(agent: Agent): Record<string, unknown> {
  const value = agent.draftConfig.conversation;
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function statusClass(status: Device["status"]): string {
  if (status === "online") return "";
  return status === "idle" ? "neutral" : "off";
}

function screenClass(status: Device["status"]): string {
  if (status === "online") return "";
  return status === "idle" ? "clay" : "smoke";
}

function renderDevices(root: HTMLElement, devices: Device[], agents: Agent[]): void {
  const agentsById = new Map(agents.map((agent) => [agent.id, agent.name]));
  const grid = query<HTMLElement>(root, ".device-grid");
  if (grid) {
    grid.innerHTML = devices.length
      ? devices
          .map((device, index) => {
            const drift = device.desiredState.version !== device.reportedState.version;
            return `<article class="device-card ${index === 0 ? "selected" : ""}">
              <div class="device-screen ${screenClass(device.status)}"><span>${device.status === "online" ? "◉" : device.status === "idle" ? "◌" : "×"}</span><div class="face"><i></i><i></i></div><small>${escapeHtml(device.status)}</small></div>
              <div class="device-copy"><div><span class="status-pill ${statusClass(device.status)}"><i></i> ${escapeHtml(device.status)}</span><b>${escapeHtml(device.name)}</b><small>${escapeHtml(device.hardwareId)}</small></div>
              <dl><div><dt>Firmware</dt><dd>${escapeHtml(device.firmwareVersion ?? "—")}</dd></div><div><dt>Agent</dt><dd>${escapeHtml(device.agentId ? agentsById.get(device.agentId) ?? "Không rõ" : "Chưa gán")}</dd></div><div><dt>Cấu hình</dt><dd>${drift ? `Drift ${device.reportedState.version}→${device.desiredState.version}` : `v${device.reportedState.version}`}</dd></div></dl>
              <button class="button button-ghost" data-page-link="mcp" type="button">Mở thiết bị →</button></div></article>`;
          })
          .join("")
      : '<article class="panel empty-state"><b>Chưa có thiết bị</b><small>Bật robot để nhận mã ghép 6 số, sau đó ghép tại đây.</small></article>';
  }

  const fleet = query<HTMLElement>(root, ".fleet-card");
  if (fleet) {
    fleet.innerHTML = `<div class="card-topline"><div><span class="eyebrow">FLEET</span><h2>Thiết bị</h2></div><span class="big-number">${String(devices.length).padStart(2, "0")}</span></div>${devices
      .slice(0, 3)
      .map(
        (device, index) =>
          `<div class="device-mini ${device.status}"><span class="device-glyph ${index === 1 ? "warm" : index > 1 ? "gray" : ""}">V${index + 1}</span><div><b>${escapeHtml(device.name)}</b><small>${escapeHtml(device.firmwareVersion ? `ESP32-S3 · ${device.firmwareVersion}` : device.hardwareId)}</small></div><span>${escapeHtml(device.status)}</span></div>`,
      )
      .join("")}<button class="text-link" data-page-link="devices">Xem toàn bộ thiết bị <b>→</b></button>`;
  }

  const count = query<HTMLElement>(root, '.nav-item[data-page-link="devices"] .nav-count');
  if (count) count.textContent = String(devices.length);
}

function renderAgents(root: HTMLElement, agents: Agent[], providers: Provider[]): void {
  const poster = query<HTMLElement>(root, ".agent-poster");
  const panel = query<HTMLElement>(root, ".config-panel");
  const agent = agents[0];
  if (!poster || !panel) return;
  if (!agent) {
    poster.innerHTML = '<span class="poster-tag">CHƯA CÓ AGENT</span><div class="poster-face"><i></i><i></i><b>⌣</b></div><h2>Hãy tạo trợ lý đầu tiên</h2><p>Persona, locale và provider đều được lưu theo version.</p>';
    panel.innerHTML = '<div class="empty-state"><b>Chưa có cấu hình để chỉnh sửa.</b></div>';
    return;
  }
  const conversation = conversationConfig(agent);
  const firstInput = Number(conversation.firstInputSeconds ?? 15);
  const betweenTurns = Number(conversation.betweenTurnsSeconds ?? 30);
  const closingGrace = Number(conversation.closingGraceSeconds ?? 5);
  const maxSession = Number(conversation.maxSessionSeconds ?? 600);
  const timeoutGoodbye = String(conversation.timeoutGoodbye ?? "Tạm biệt, hẹn gặp lại.");
  const providerChainRows = ["vad", "asr", "llm", "tts"]
    .map((kind) => renderAgentProviderChain(agent, providers, kind))
    .join("");
  poster.innerHTML = `<span class="poster-tag">PUBLISHED · V${agent.publishedVersion}</span><div class="poster-face"><i></i><i></i><b>⌣</b></div><h2>${escapeHtml(agent.name)}</h2><p>${escapeHtml(agent.persona)}</p><div class="poster-meta"><span>${escapeHtml(agent.defaultLocale)}</span><span>${escapeHtml(agent.interactionMode)}</span><span>version ${agent.publishedVersion}</span></div>`;
  panel.dataset.agentId = agent.id;
  panel.innerHTML = `<div class="tabs"><button class="active">Hành vi</button><button>Providers</button><button>Ngôn ngữ</button><button>Tools</button></div>
    <label>Tên trợ lý<input data-agent-field="name" value="${escapeHtml(agent.name)}"></label>
    <label>System prompt<textarea data-agent-field="persona">${escapeHtml(agent.persona)}</textarea></label>
    <div class="two-cols"><label>Locale mặc định<select data-agent-field="locale"><option value="vi-VN" ${agent.defaultLocale === "vi-VN" ? "selected" : ""}>Tiếng Việt (vi-VN)</option><option value="en-US" ${agent.defaultLocale === "en-US" ? "selected" : ""}>English (en-US)</option></select></label>
    <label>Chế độ hội thoại<select data-agent-field="mode"><option value="auto" ${agent.interactionMode === "auto" ? "selected" : ""}>Cascade realtime · auto</option><option value="realtime" ${agent.interactionMode === "realtime" ? "selected" : ""}>End-to-end realtime · auto</option><option value="manual" ${agent.interactionMode === "manual" ? "selected" : ""}>Manual/PTT compatibility</option></select></label></div>
    <div class="two-cols"><label>Chờ câu đầu (giây)<input data-agent-field="first-input" type="number" min="3" max="300" value="${firstInput}"></label><label>Giữa các lượt (giây)<input data-agent-field="between-turns" type="number" min="3" max="600" value="${betweenTurns}"></label></div>
    <div class="two-cols"><label>Closing grace (giây)<input data-agent-field="closing-grace" type="number" min="0.5" max="60" step="0.5" value="${closingGrace}"></label><label>Giới hạn phiên (giây)<input data-agent-field="max-session" type="number" min="10" max="3600" value="${maxSession}"></label></div>
    <label>Lời chào khi hết thời gian<input data-agent-field="timeout-goodbye" maxlength="240" value="${escapeHtml(timeoutGoodbye)}"></label>
    <div class="two-cols"><label>Input admission<select disabled><option>Semantic + quality gate</option></select></label><label>TurnArbiter<select disabled><option>Generation cancellation</option></select></label></div>
    <span class="eyebrow">PROVIDER CHAINS · ${escapeHtml(agent.defaultLocale)}</span>
    ${providerChainRows}
    <div class="publish-bar"><div><span class="unsaved-dot"></span><p><b>Lưu draft và publish immutable version</b><small>Thiết bị đang dùng version ${agent.publishedVersion}</small></p></div><button class="button button-primary publish-agent" type="button">Publish version ${agent.version + 1}</button></div>`;

  const select = query<HTMLSelectElement>(root, "#pairModal select");
  if (select) {
    select.innerHTML = `${agents.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · v${item.publishedVersion}</option>`).join("")}<option value="">Chưa gán trợ lý</option>`;
  }
}

function renderProviders(root: HTMLElement, providers: Provider[]): void {
  providerCatalogs.set(root, providers);
  const table = query<HTMLElement>(root, ".provider-table");
  if (table) {
    table.innerHTML = `<div class="provider-row table-head"><span>Provider</span><span>Loại</span><span>Locale / model</span><span>Latency</span><span>Health / circuit</span><span></span></div>${providers
      .map(
        (provider) => `<div class="provider-row"><span><i class="provider-logo local">${escapeHtml(provider.kind.slice(0, 2).toUpperCase())}</i><b>${escapeHtml(provider.adapter)}</b></span><span>${escapeHtml(provider.kind.toUpperCase())}<small>P${provider.priority}</small></span><span>${escapeHtml(provider.model)}<small>${escapeHtml(provider.locales.join(", "))} · ${provider.secretConfigured ? "secret" : "no secret"}</small></span><span>${provider.healthLatencyMs !== undefined ? `${provider.healthLatencyMs} ms` : "—"}<small>${provider.healthCheckedAt ? new Date(provider.healthCheckedAt).toLocaleTimeString("vi-VN") : "chưa test"}</small></span><span class="health-text ${provider.health === "healthy" ? "ok-text" : provider.health === "degraded" ? "warn-text" : ""}">${escapeHtml(provider.health)} · ${escapeHtml(provider.circuitState)}${provider.healthErrorCode ? `<small>${escapeHtml(provider.healthErrorCode)}</small>` : ""}</span><span><button class="edit-provider" data-provider-id="${escapeHtml(provider.id)}" type="button">Sửa</button> <button class="test-provider" data-provider-id="${escapeHtml(provider.id)}" type="button">Test</button></span></div>`,
      )
      .join("")}`;
  }
  const healthy = providers.filter((provider) => provider.health === "healthy").length;
  const summary = query<HTMLElement>(root, ".provider-summary");
  if (summary) {
    const percent = providers.length ? Math.round((healthy / providers.length) * 100) : 0;
    summary.innerHTML = `<div><small>Đang healthy</small><b>${healthy} / ${providers.length}</b><span class="bar"><i style="width:${percent}%"></i></span></div><div><small>Local providers</small><b>${providers.filter((item) => !item.baseUrl || item.baseUrl.includes("127.0.0.1")).length}</b><em>Không rời khỏi máy chủ</em></div><div><small>Model registry</small><b>${new Set(providers.map((item) => item.kind)).size} capability</b><em>Vietnamese-first</em></div>`;
  }
}

function renderAgentProviderChain(agent: Agent, providers: Provider[], kind: string): string {
  const chains = Array.isArray(agent.draftConfig.providerChains)
    ? agent.draftConfig.providerChains
    : [];
  const chain = chains.find(
    (value) =>
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      (value as Record<string, unknown>).kind === kind &&
      (value as Record<string, unknown>).locale === agent.defaultLocale,
  ) as Record<string, unknown> | undefined;
  const ids = Array.isArray(chain?.providerIds)
    ? chain.providerIds.filter((value): value is string => typeof value === "string")
    : [];
  const compatible = providers.filter(
    (provider) =>
      provider.kind === kind &&
      provider.enabled &&
      (provider.locales.includes("*") || provider.locales.includes(agent.defaultLocale)),
  );
  const options = (selectedId?: string) =>
    compatible
      .map(
        (provider) =>
          `<option value="${escapeHtml(provider.id)}" ${provider.id === selectedId ? "selected" : ""}>P${provider.priority} · ${escapeHtml(provider.adapter)} · ${escapeHtml(provider.model)}</option>`,
      )
      .join("");
  return `<div class="two-cols"><label>${kind.toUpperCase()} primary<select data-provider-chain-kind="${kind}" data-provider-chain-role="primary"><option value="">Chọn provider</option>${options(ids[0])}</select></label><label>${kind.toUpperCase()} fallback<select data-provider-chain-kind="${kind}" data-provider-chain-role="fallback"><option value="">Không dùng fallback</option>${options(ids[1])}</select></label></div>`;
}

function schemaProperties(tool: McpTool): Record<string, Record<string, unknown>> {
  const properties = tool.inputSchema.properties;
  if (!properties || typeof properties !== "object" || Array.isArray(properties)) return {};
  return Object.fromEntries(
    Object.entries(properties).filter(
      (entry): entry is [string, Record<string, unknown>] =>
        Boolean(entry[1]) && typeof entry[1] === "object" && !Array.isArray(entry[1]),
    ),
  );
}

function renderToolArgument(
  name: string,
  schema: Record<string, unknown>,
  required: boolean,
): string {
  const label = escapeHtml(schema.title ?? name);
  const description = schema.description
    ? `<small>${escapeHtml(schema.description)}</small>`
    : "";
  const requiredAttribute = required ? " required" : "";
  const values = Array.isArray(schema.enum) ? schema.enum : [];
  if (values.length) {
    return `<label>${label}${description}<select data-tool-argument="${escapeHtml(name)}"${requiredAttribute}>${values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")}</select></label>`;
  }
  if (schema.type === "boolean") {
    return `<label class="tool-checkbox"><input data-tool-argument="${escapeHtml(name)}" data-argument-kind="boolean" type="checkbox"> ${label}${description}</label>`;
  }
  if (schema.type === "integer" || schema.type === "number") {
    const step = schema.type === "integer" ? "1" : "any";
    const minimum = typeof schema.minimum === "number" ? ` min="${schema.minimum}"` : "";
    const maximum = typeof schema.maximum === "number" ? ` max="${schema.maximum}"` : "";
    return `<label>${label}${description}<input data-tool-argument="${escapeHtml(name)}" data-argument-kind="number" type="number" step="${step}"${minimum}${maximum}${requiredAttribute}></label>`;
  }
  if (schema.type === "string") {
    return `<label>${label}${description}<input data-tool-argument="${escapeHtml(name)}" type="text"${requiredAttribute}></label>`;
  }
  return `<label>${label}${description}<textarea data-tool-argument="${escapeHtml(name)}" data-argument-kind="json"${requiredAttribute}>{}</textarea></label>`;
}

function renderToolDetail(
  root: HTMLElement,
  tool: McpTool | undefined,
  activeDeviceId: string | undefined,
  live: boolean,
): void {
  const detail = query<HTMLElement>(root, ".tool-detail");
  if (!detail) return;
  if (!tool) {
    detail.innerHTML = '<div class="empty-state"><b>Chưa có MCP tool.</b></div>';
    return;
  }
  const required = new Set(
    Array.isArray(tool.inputSchema.required)
      ? tool.inputSchema.required.filter((item): item is string => typeof item === "string")
      : [],
  );
  const inputs = Object.entries(schemaProperties(tool))
    .map(([name, schema]) => renderToolArgument(name, schema, required.has(name)))
    .join("");
  const confirmation = tool.requiresConfirmation
    ? '<label class="tool-checkbox confirmation"><input data-tool-confirmed type="checkbox" required> Tôi xác nhận thực thi thao tác này trên thiết bị.</label>'
    : "";
  detail.innerHTML = `<span class="status-pill ${live ? "" : "neutral"}"><i></i> ${live ? "Live device catalog" : "Baseline capability"}</span><h2>${escapeHtml(tool.name)}</h2><p>${escapeHtml(tool.description)}</p><div class="tool-policy"><span>${escapeHtml(tool.audience === "regular" ? "AI-callable" : "User-only")}</span><span>${escapeHtml(tool.safetyClass)}</span></div><form class="tool-call-form" data-tool-name="${escapeHtml(tool.name)}" data-device-id="${escapeHtml(activeDeviceId ?? "")}">${inputs || '<small class="audit-note">Tool này không cần tham số.</small>'}${confirmation}<button class="button button-primary run-device-tool" type="button" ${!activeDeviceId || !live ? "disabled" : ""}>${activeDeviceId && live ? "Chạy trên thiết bị" : "Thiết bị chưa có phiên voice active"}</button></form><div class="schema-block"><span>INPUT SCHEMA</span><pre>${escapeHtml(JSON.stringify(tool.inputSchema, null, 2))}</pre></div><small class="audit-note">Mọi tool call đều được kiểm tra catalog live, confirmation policy và ghi audit.</small>`;
}

function renderTools(
  root: HTMLElement,
  tools: McpTool[],
  activeDeviceId: string | undefined,
  live: boolean,
): void {
  const list = query<HTMLElement>(root, ".tool-list");
  if (!list) return;
  toolCatalogs.set(root, { tools, activeDeviceId, live });
  list.innerHTML = tools
    .map(
      (tool, index) => `<button class="tool-item ${index === 0 ? "active" : ""}" data-tool-name="${escapeHtml(tool.name)}" type="button"><span class="tool-type ${tool.audience === "regular" ? "safe" : "user"}">${tool.audience === "regular" ? "AI" : "USER"}</span><div><b>${escapeHtml(tool.name)}</b><small>${escapeHtml(tool.description)}</small></div><i>→</i></button>`,
    )
    .join("");
  renderToolDetail(root, tools[0], activeDeviceId, live);
}

function renderOverview(root: HTMLElement, data: ManagerViewData): void {
  const agent = data.agents[0];
  const heading = query<HTMLElement>(root, ".heading-meta");
  if (heading) {
    heading.innerHTML = `<span>Phiên bản cấu hình</span><b>${agent ? `${escapeHtml(agent.name)}.v${agent.publishedVersion}` : "chưa publish"}</b><small>Immutable agent snapshot</small>`;
  }
  const metrics = query<HTMLElement>(root, ".metric-strip");
  const asr = data.providers.find((provider) => provider.kind === "asr");
  const tts = data.providers.find((provider) => provider.kind === "tts");
  if (metrics) {
    metrics.innerHTML = `<div><span class="metric-symbol coral">A</span><p><small>ASR tiếng Việt</small><b>${escapeHtml(asr?.model ?? "Chưa cấu hình")}</b><em>${escapeHtml(asr?.adapter ?? "provider registry")}</em></p></div><div><span class="metric-symbol blue">T</span><p><small>TTS local</small><b>${escapeHtml(tts?.model ?? "Chưa cấu hình")}</b><em>${escapeHtml(tts?.health ?? "unknown")}</em></p></div><div><span class="metric-symbol lime">M</span><p><small>MCP tools</small><b>${data.tools.length} ready</b><em>${data.tools.filter((tool) => tool.audience === "user").length} user-only</em></p></div><div><span class="metric-symbol ink">C</span><p><small>Config drift</small><b>${data.devices.filter((device) => device.desiredState.version !== device.reportedState.version).length} device</b><em>desired vs reported</em></p></div>`;
  }
  const healthyProviders = data.providers.filter((provider) => provider.health === "healthy").length;
  const providerPercent = data.providers.length
    ? Math.round((healthyProviders / data.providers.length) * 100)
    : 0;
  const healthRing = query<HTMLElement>(root, ".health-ring > div");
  if (healthRing) {
    healthRing.innerHTML = `<b>${providerPercent}</b><span>%</span><small>registry health</small>`;
  }
  const healthList = query<HTMLElement>(root, ".health-list");
  if (healthList) {
    healthList.innerHTML = data.providers
      .slice(0, 3)
      .map((provider) => `<span><i class="${provider.health === "healthy" ? "ok" : "warn"}"></i> ${escapeHtml(provider.kind.toUpperCase())} <b>${escapeHtml(provider.health)}</b></span>`)
      .join("");
  }
}

function eventDetail(event: ConversationEvent): string {
  const payload = event.payload;
  if (event.eventType === "listen.start") {
    return `Assistant gate · source=${String(payload.source ?? "unknown")}`;
  }
  if (event.eventType === "stt.final") {
    return `${String(payload.locale ?? "unknown")} · ${Number(payload.character_count ?? 0)} ký tự · transcript đã redact`;
  }
  if (event.eventType === "admission") {
    const confidence = Math.round(Number(payload.confidence ?? 0) * 100);
    return `${String(payload.disposition ?? "unknown")} · ${String(payload.reason_code ?? "unknown")} · ${confidence}%`;
  }
  if (event.eventType === "plan") {
    const tool = payload.tool_name ? ` · tool=${String(payload.tool_name)}` : "";
    return `${String(payload.action ?? "unknown")} · ${String(payload.intent ?? "unknown")}${tool}`;
  }
  if (event.eventType === "abort" || event.eventType === "assistant.sleep") {
    return `reason=${String(payload.reason ?? "unknown")} · generation=${event.generation}`;
  }
  if (event.eventType === "tts.start") return "Bắt đầu phát audio xuống thiết bị";
  if (event.eventType === "tts.stop") return "Kết thúc playback của generation hiện tại";
  if (event.eventType === "error") {
    return `${String(payload.code ?? "unknown_error")} · stage=${String(payload.stage ?? "conversation")}`;
  }
  return JSON.stringify(payload).slice(0, 180);
}

function elapsedMilliseconds(origin: ConversationEvent, event: ConversationEvent): number {
  return Math.max(0, Date.parse(event.occurredAt) - Date.parse(origin.occurredAt));
}

function renderTelemetry(root: HTMLElement, data: ManagerViewData): void {
  const events = data.conversationEvents;
  const latest = events.at(-1);
  const sessionEvents = latest
    ? events.filter((event) => event.sessionId === latest.sessionId)
    : [];
  const origin = sessionEvents[0];
  const latestAge = latest ? Date.now() - Date.parse(latest.occurredAt) : Number.POSITIVE_INFINITY;
  const recent = latestAge >= 0 && latestAge < 10_000;
  const voiceStatus = query<HTMLElement>(root, ".voice-card .status-pill");
  if (voiceStatus) {
    voiceStatus.classList.toggle("neutral", !recent);
    voiceStatus.innerHTML = `<i></i> ${recent ? "Voice telemetry live" : events.length ? "Voice telemetry idle" : "Chưa có telemetry"}`;
  }

  const stt = [...sessionEvents].reverse().find((event) => event.eventType === "stt.final");
  const firstAudio = stt
    ? sessionEvents.find(
        (event) => event.eventType === "tts.start" && Date.parse(event.occurredAt) >= Date.parse(stt.occurredAt),
      )
    : undefined;
  const firstAudioMs = stt && firstAudio ? elapsedMilliseconds(stt, firstAudio) : undefined;
  const today = new Date().toISOString().slice(0, 10);
  const sessionsToday = new Set(
    events.filter((event) => event.occurredAt.slice(0, 10) === today).map((event) => event.sessionId),
  ).size;
  const latency = query<HTMLElement>(root, ".voice-card .latency-row");
  if (latency) {
    latency.innerHTML = `<div><span>First audio</span><b>${firstAudioMs ?? "—"}${firstAudioMs !== undefined ? "<small>ms</small>" : ""}</b><em>${firstAudioMs !== undefined ? "từ ASR final" : "chờ turn có TTS"}</em></div><div><span>Ngắt lời</span><b>—</b><em>cần playback ACK/AEC</em></div><div><span>Phiên hôm nay</span><b>${sessionsToday}</b><em>${events.length} event đã redact</em></div>`;
  }

  const traceId = query<HTMLElement>(root, ".trace-id");
  if (traceId) {
    const turnId = [...sessionEvents].reverse().find((event) => event.turnId)?.turnId;
    traceId.textContent = turnId ? turnId.slice(-18).toUpperCase() : latest ? latest.sessionId.slice(0, 12).toUpperCase() : "NO LIVE TURN";
  }
  const timeline = query<HTMLElement>(root, ".timeline");
  if (timeline) {
    timeline.innerHTML = origin
      ? sessionEvents
          .slice(-12)
          .map(
            (event, index) => `<div class="timeline-item ${index === sessionEvents.slice(-12).length - 1 && recent ? "active" : "done"}"><span>${String(index).padStart(2, "0")}</span><i></i><div><b>${escapeHtml(event.eventType)}</b><small>${escapeHtml(eventDetail(event))}</small></div><em>${elapsedMilliseconds(origin, event)} ms</em></div>`,
          )
          .join("")
      : '<div class="empty-state"><b>Chưa có conversation event thật.</b><small>Hãy gọi robot bằng nút hoặc “Hey VeeTee”; timeline tự đồng bộ và không lưu transcript/audio.</small></div>';
  }

  const labState = query<HTMLElement>(root, "#labState");
  const labToggle = query<HTMLButtonElement>(root, "#labToggle");
  const labPrompt = query<HTMLElement>(root, "#labPrompt");
  const labOrb = query<HTMLElement>(root, "#labOrb");
  if (labState) {
    labState.classList.toggle("running", recent);
    labState.innerHTML = `<i></i> ${recent ? "Đang nhận event" : "Đang quan sát"}`;
  }
  if (labToggle) labToggle.textContent = "Timeline tự đồng bộ";
  if (labPrompt) {
    labPrompt.textContent = data.activeDeviceId
      ? "Bấm nút trên robot hoặc nói “Hey VeeTee” để mở assistant; VAD tự kết thúc câu và event sẽ xuất hiện ở đây."
      : "Ghép một thiết bị để bắt đầu nhận conversation telemetry.";
  }
  labOrb?.classList.toggle("running", recent);
  const interruptButton = query<HTMLButtonElement>(root, "#interruptButton");
  if (interruptButton) {
    interruptButton.disabled = true;
    interruptButton.title = "Remote interrupt chưa có device command contract; nút vật lý vẫn là guarantee.";
  }
  const eventLog = query<HTMLElement>(root, "#eventLog");
  if (eventLog) {
    eventLog.innerHTML = origin
      ? sessionEvents
          .slice(-20)
          .map(
            (event) => `<div class="event-entry"><i></i><div><b>${escapeHtml(event.eventType)}</b><small>${escapeHtml(eventDetail(event))}</small></div><em>+${elapsedMilliseconds(origin, event)} ms</em></div>`,
          )
          .join("")
      : '<div class="empty-event"><span>⌁</span><b>Chưa có phiên thật</b><small>Không còn timeline mô phỏng; dữ liệu sẽ đến từ voice-server.</small></div>';
  }
}

function reportedResourceVersion(device: Device): string | undefined {
  const resource = device.reportedState.state.resource;
  if (!resource || typeof resource !== "object" || Array.isArray(resource)) return undefined;
  const version = (resource as Record<string, unknown>).currentVersion;
  return typeof version === "string" ? version : undefined;
}

function renderResources(root: HTMLElement, data: ManagerViewData): void {
  const latestArtifact = data.artifacts.find((artifact) => artifact.status === "published");
  const releaseHero = query<HTMLElement>(root, ".release-hero");
  if (releaseHero) {
    if (!latestArtifact) {
      releaseHero.innerHTML = '<div><span class="status-pill neutral"><i></i> CHƯA CÓ ARTIFACT PUBLISHED</span><h2>Đăng ký release đã ký từ artifact storage</h2><p>Manager chỉ nhận catalog metadata sau khi kiểm tra file immutable, SHA-256, restricted JCS, Ed25519, board, ABI và route canonical.</p><div class="release-meta"><span><small>Executable</small><b>không cho phép</b></span><span><small>Resource ABI</small><b>data/model only</b></span><span><small>Rollout</small><b>desired ≠ active</b></span></div></div><div class="rollout-ring"><b>0%</b><span>0 thiết bị</span><small>active</small></div>';
    } else {
      const desired = data.devices.filter(
        (device) => device.desiredState.state.resourceManifestId === latestArtifact.id,
      );
      const active = desired.filter(
        (device) => reportedResourceVersion(device) === latestArtifact.version,
      ).length;
      const percent = desired.length ? Math.round((active / desired.length) * 100) : 0;
      releaseHero.innerHTML = `<div><span class="status-pill"><i></i> ${escapeHtml(latestArtifact.status.toUpperCase())} · ${escapeHtml(latestArtifact.channel)}</span><h2>Resource ${escapeHtml(latestArtifact.version)}</h2><p>${escapeHtml(latestArtifact.kind)} · ${escapeHtml(latestArtifact.runtime)} ABI ${latestArtifact.runtimeAbi} · benchmark ${escapeHtml(latestArtifact.benchmarkStatus)}</p><div class="release-meta"><span><small>SHA-256</small><b>${escapeHtml(latestArtifact.sha256.slice(0, 12))}…</b></span><span><small>Signature</small><b>${escapeHtml(latestArtifact.signatureKeyId)} · epoch ${latestArtifact.securityEpoch}</b></span><span><small>License</small><b>${escapeHtml(latestArtifact.license)}</b></span></div></div><div class="rollout-ring"><b>${percent}%</b><span>${active} / ${desired.length} thiết bị</span><small>reported active</small></div>`;
    }
  }

  const releaseList = query<HTMLElement>(root, ".release-list");
  if (!releaseList) return;
  const canAdmin = ["OWNER", "ADMIN"].includes(data.principal.role);
  const publishedArtifacts = data.artifacts.filter((artifact) => artifact.status === "published");
  const artifactOptions = publishedArtifacts
    .map(
      (artifact) => `<option value="${escapeHtml(artifact.id)}">${escapeHtml(artifact.id)} · ${escapeHtml(artifact.version)}</option>`,
    )
    .join("");
  const registerForm = canAdmin
    ? `<form class="config-panel resource-form artifact-register-form"><span class="eyebrow">SIGNED ARTIFACT CATALOG</span><div class="two-cols"><label>Artifact ID<input name="artifactId" value="stable" maxlength="64" required></label><label>License / provenance<input name="license" value="ESP-SR bring-up model pack; verify production redistribution" maxlength="120" required></label></div><button class="button button-primary" type="submit">Đăng ký và xác minh</button><small class="audit-note">Binary phải được tạo trước bằng release signer; Web không upload runtime/native code.</small></form>`
    : "";
  const artifactRows = data.artifacts
    .map(
      (artifact) => `<div class="release-row"><span class="release-version">${escapeHtml(artifact.version)}</span><div><b>${escapeHtml(artifact.id)} · ${escapeHtml(artifact.status)}</b><small>${escapeHtml(artifact.runtime)} ABI ${artifact.runtimeAbi} · ${(artifact.sizeBytes / 1024).toFixed(1)} KiB · ${escapeHtml(artifact.benchmarkStatus)}</small></div><span class="release-date">${escapeHtml(artifact.channel)}</span>${artifact.status === "validated" && canAdmin ? `<button class="publish-artifact" data-artifact-id="${escapeHtml(artifact.id)}" type="button">Publish →</button>` : ""}</div>`,
    )
    .join("");
  const wakeForm = publishedArtifacts.length
    ? `<form class="config-panel resource-form wake-profile-form"><span class="eyebrow">WAKE PROFILE DRAFT</span><div class="two-cols"><label>Tên profile<input name="name" value="Hey VeeTee development" maxlength="80" required></label><label>Artifact<select name="artifactId">${artifactOptions}</select></label></div><div class="two-cols"><label>Activation phrase<input name="activationPhrase" value="Hey VeeTee" maxlength="80" required></label><label>Channel<select name="channel"><option value="development">development</option><option value="canary">canary</option><option value="stable">stable (cần benchmark pass)</option></select></label></div><div class="two-cols"><label>Activation detector ID<input name="activationDetectorId" placeholder="wakenet:model_id" required></label><label>Interrupt detector ID<input name="interruptDetectorId" placeholder="multinet:model_id" required></label></div><button class="button button-primary" type="submit">Tạo wake profile draft</button><small class="audit-note">Nhập phrase không tự tạo model. “Hey VeeTee” chỉ product-ready sau corpus FAR/FRR/latency pass.</small></form>`
    : "";
  const profileRows = data.wakeProfiles
    .map(
      (profile) => `<div class="release-row"><span class="release-version ${profile.productReady ? "" : "old"}">W${profile.publishedVersion || profile.version}</span><div><b>${escapeHtml(profile.name)} · ${escapeHtml(profile.activationPhrase)}</b><small>${escapeHtml(profile.channel)} · activation ${escapeHtml(profile.activation.detectorId)} · interrupt ${escapeHtml(profile.interrupt.detectorId)} · ${profile.productReady ? "product-ready" : "bring-up/not benchmarked"}</small></div><span class="release-date">${profile.publishedVersion ? `published v${profile.publishedVersion}` : "draft"}</span>${profile.publishedVersion === 0 ? `<button class="publish-wake-profile" data-wake-profile-id="${profile.id}" type="button">Publish →</button>` : data.activeDeviceId ? `<button class="rollout-wake-profile" data-wake-profile-id="${profile.id}" data-device-id="${data.activeDeviceId}" type="button">Rollout →</button>` : ""}</div>`,
    )
    .join("");
  const rolloutRows = data.resourceRollouts
    .slice(0, 5)
    .map(
      (rollout) => `<div class="release-row"><span class="release-version old">R</span><div><b>${escapeHtml(rollout.artifactId)} → ${escapeHtml(rollout.deviceId.slice(0, 8))}</b><small>wake v${rollout.wakeProfileVersion} · desired state v${rollout.desiredStateVersion}</small></div><span class="release-date">${escapeHtml(rollout.status)}</span><button type="button" disabled>reported ≠ assumed</button></div>`,
    )
    .join("");
  releaseList.innerHTML = `${registerForm}${artifactRows || '<div class="empty-state"><b>Catalog trống.</b><small>Đăng ký artifact đã ký trong storage để bắt đầu.</small></div>'}${wakeForm}${profileRows}${rolloutRows}`;
}

function renderAvailability(root: HTMLElement, data: ManagerViewData): void {
  const device = data.devices[0];
  const agent = data.agents[0];
  const consoleTop = query<HTMLElement>(root, ".console-top");
  if (consoleTop) {
    consoleTop.innerHTML = `<div><span>DEVICE</span><b>${escapeHtml(device?.name ?? "Chưa có thiết bị")}</b></div><div><span>AGENT</span><b>${escapeHtml(agent ? `${agent.name} · v${agent.publishedVersion}` : "Chưa có agent")}</b></div><div><span>ENGINE</span><b>${escapeHtml(agent ? `${agent.interactionMode} · auto gate` : "Cascade · auto")}</b></div>`;
  }
}

export function renderManagerView(root: HTMLElement, data: ManagerViewData): void {
  const profileName = query<HTMLElement>(root, ".profile-card b");
  const profileRole = query<HTMLElement>(root, ".profile-card small");
  const avatar = query<HTMLElement>(root, ".profile-card .avatar");
  if (profileName) profileName.textContent = data.principal.displayName;
  if (profileRole) profileRole.textContent = `${data.principal.role.toLowerCase()} · ${data.principal.tenantSlug}`;
  if (avatar) {
    avatar.textContent = data.principal.displayName
      .split(/\s+/)
      .slice(-2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("");
  }
  const lan = query<HTMLElement>(root, ".sidebar-note strong");
  const health = query<HTMLElement>(root, ".mini-health");
  if (lan) lan.textContent = data.apiHost;
  if (health) {
    health.innerHTML = `<i></i> ${data.ready ? "Manager API sẵn sàng" : "Manager API suy giảm"}`;
    health.classList.toggle("degraded", !data.ready);
  }
  const signatures = renderSignatures.get(root) ?? {};
  const devicesSignature = JSON.stringify([data.devices, data.agents.map((agent) => [agent.id, agent.name])]);
  const agentsSignature = JSON.stringify([data.agents, data.providers]);
  const providersSignature = JSON.stringify(data.providers);
  const toolsSignature = JSON.stringify([
    data.tools,
    data.activeDeviceId,
    data.toolsLive,
  ]);
  if (signatures.devices !== devicesSignature) {
    renderDevices(root, data.devices, data.agents);
    signatures.devices = devicesSignature;
  }
  if (signatures.agents !== agentsSignature) {
    renderAgents(root, data.agents, data.providers);
    signatures.agents = agentsSignature;
  }
  if (signatures.providers !== providersSignature) {
    renderProviders(root, data.providers);
    signatures.providers = providersSignature;
  }
  if (signatures.tools !== toolsSignature) {
    renderTools(root, data.tools, data.activeDeviceId, data.toolsLive);
    signatures.tools = toolsSignature;
  }
  renderSignatures.set(root, signatures);
  renderOverview(root, data);
  renderTelemetry(root, data);
  renderResources(root, data);
  renderAvailability(root, data);
}

export function initializePrototype(
  root: HTMLElement,
  callbacks: PrototypeCallbacks,
): PrototypeController {
  const abort = new AbortController();
  const signal = abort.signal;
  let toastTimer = 0;

  if (!query(root, "#providerModal")) {
    root.insertAdjacentHTML(
      "beforeend",
      `<div class="modal-backdrop" id="providerModal" hidden><div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="providerTitle"><button class="modal-close" type="button" data-close-provider-modal aria-label="Đóng">×</button><span class="modal-kicker">PROVIDER BINDING</span><h2 id="providerTitle">Cấu hình provider</h2><form class="provider-edit-form"><input type="hidden" name="providerId"><label>Adapter<input name="adapter" required maxlength="120"></label><label>Model<input name="model" required maxlength="200"></label><label>Base URL<input name="baseUrl" placeholder="http://127.0.0.1:20128/v1"></label><div class="two-cols"><label>Priority<input name="priority" type="number" min="0" max="1000" required></label><label>Locales<input name="locales" placeholder="vi-VN, en-US" required></label></div><div class="two-cols"><label>Trạng thái<select name="enabled"><option value="true">Enabled</option><option value="false">Disabled</option></select></label><label>Secret<select name="secretAction"><option value="keep">Giữ nguyên</option><option value="rotate">Thay secret</option><option value="clear">Xóa secret</option></select></label></div><label>Secret mới<input name="secret" type="password" autocomplete="new-password" placeholder="Chỉ nhập khi chọn Thay secret"></label><button class="button button-primary modal-submit" type="submit">Lưu provider</button></form></div></div>`,
    );
  }

  const listen = (target: EventTarget | null, event: string, handler: EventListener): void => {
    target?.addEventListener(event, handler, { signal });
  };

  const toast = (message: string): void => {
    const element = query<HTMLElement>(root, "#toast");
    if (!element) return;
    element.textContent = message;
    element.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => element.classList.remove("show"), 2_800);
  };

  const showPage = (page: string): void => {
    const selected = pageNames[page] ? page : "overview";
    queryAll<HTMLElement>(root, ".page").forEach((section) =>
      section.classList.toggle("active", section.dataset.page === selected),
    );
    queryAll<HTMLElement>(root, ".nav-item").forEach((item) =>
      item.classList.toggle("active", item.dataset.pageLink === selected),
    );
    const crumb = query<HTMLElement>(root, "#pageCrumb");
    if (crumb) crumb.textContent = pageNames[selected] ?? "Tổng quan";
    history.replaceState(null, "", `#${selected}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const openPairing = (): void => {
    const modal = query<HTMLElement>(root, "#pairModal");
    if (modal) modal.hidden = false;
    window.setTimeout(() => query<HTMLInputElement>(root, "#codeInputs input")?.focus(), 80);
  };

  const closePairing = (): void => {
    const modal = query<HTMLElement>(root, "#pairModal");
    if (modal) modal.hidden = true;
  };

  const closeProvider = (): void => {
    const modal = query<HTMLElement>(root, "#providerModal");
    if (modal) modal.hidden = true;
  };

  const openProvider = (provider: Provider): void => {
    const modal = query<HTMLElement>(root, "#providerModal");
    const form = query<HTMLFormElement>(root, ".provider-edit-form");
    if (!modal || !form) return;
    const set = (name: string, value: string): void => {
      const input = query<HTMLInputElement | HTMLSelectElement>(form, `[name="${name}"]`);
      if (input) input.value = value;
    };
    set("providerId", provider.id);
    set("adapter", provider.adapter);
    set("model", provider.model);
    set("baseUrl", provider.baseUrl ?? "");
    set("priority", String(provider.priority));
    set("locales", provider.locales.join(", "));
    set("enabled", String(provider.enabled));
    set("secretAction", "keep");
    set("secret", "");
    modal.hidden = false;
  };

  queryAll<HTMLElement>(root, ".wave, .lab-wave").forEach((wave) => {
    if (wave.children.length) return;
    const count = wave.classList.contains("lab-wave") ? 42 : 25;
    for (let index = 0; index < count; index += 1) {
      const bar = document.createElement("i");
      bar.style.height = `${14 + Math.round(Math.random() * 52)}px`;
      bar.style.animationDelay = `${(index % 8) * -0.14}s`;
      wave.appendChild(bar);
    }
  });

  type DeviceUiLocale = keyof typeof deviceUiCopy;
  type DeviceUiState = keyof (typeof deviceUiCopy)["vi"];
  type DeviceUiTheme = keyof typeof deviceUiThemeNames;
  let deviceUiLocale: DeviceUiLocale = "vi";
  let deviceUiState: DeviceUiState = "idle";
  let deviceUiTheme: DeviceUiTheme = "signal";

  const renderDeviceUiPreview = (): void => {
    const preview = query<HTMLElement>(root, "[data-ui-preview]");
    const content = deviceUiCopy[deviceUiLocale][deviceUiState];
    if (!preview || !content) return;
    const [number, kicker, title, hint] = content;
    preview.dataset.theme = deviceUiTheme;
    preview.dataset.state = deviceUiState;
    const numberNode = query<HTMLElement>(preview, "[data-ui-number]");
    const kickerNode = query<HTMLElement>(preview, "[data-ui-kicker]");
    const titleNode = query<HTMLElement>(preview, "[data-ui-title]");
    const hintNode = query<HTMLElement>(preview, "[data-ui-hint]");
    const nameNode = query<HTMLElement>(root, "#uiPreviewName");
    if (numberNode) numberNode.textContent = number;
    if (kickerNode) kickerNode.textContent = kicker;
    if (titleNode) titleNode.textContent = title;
    if (hintNode) hintNode.textContent = hint;
    if (nameNode) nameNode.textContent = deviceUiThemeNames[deviceUiTheme];
    queryAll<HTMLButtonElement>(root, "[data-ui-state]").forEach((button) =>
      button.classList.toggle("active", button.dataset.uiState === deviceUiState),
    );
    queryAll<HTMLButtonElement>(root, "[data-ui-theme]").forEach((button) => {
      const active = button.dataset.uiTheme === deviceUiTheme;
      button.classList.toggle("active", active);
      const marker = query<HTMLElement>(button, "strong");
      if (marker) marker.textContent = active ? "✓" : "○";
    });
  };

  const uiPackInput = query<HTMLInputElement>(root, "[data-ui-pack-file]");
  const uiDropZone = query<HTMLElement>(root, ".ui-drop-zone");
  const uiInspection = query<HTMLElement>(root, "[data-ui-file-inspection]");
  const uiClearFile = query<HTMLButtonElement>(root, "[data-ui-clear-file]");
  const uiStagePack = query<HTMLButtonElement>(root, "[data-ui-stage-pack]");
  let selectedUiPack: File | undefined;
  let stagedUiArtifact: Artifact | undefined;
  let uiPackAction: "stage" | "publish" | "rollout" = "stage";

  const clearUiPack = (): void => {
    if (uiPackInput) uiPackInput.value = "";
    if (uiInspection) uiInspection.hidden = true;
    if (uiClearFile) uiClearFile.disabled = true;
    if (uiStagePack) uiStagePack.disabled = true;
    if (uiStagePack) uiStagePack.textContent = "Đưa vào staging";
    selectedUiPack = undefined;
    stagedUiArtifact = undefined;
    uiPackAction = "stage";
    const status = query<HTMLElement>(root, "[data-ui-upload-status]");
    if (status) status.textContent = "Chưa chọn file";
  };

  const inspectUiPack = async (file: File): Promise<void> => {
    const status = query<HTMLElement>(root, "[data-ui-upload-status]");
    const fileName = query<HTMLElement>(root, "[data-ui-file-name]");
    const fileSize = query<HTMLElement>(root, "[data-ui-file-size]");
    const fileHash = query<HTMLElement>(root, "[data-ui-file-hash]");
    const extensionValid = /\.(?:vtp|bin)$/i.test(file.name);
    const sizeValid = file.size > 0 && file.size <= 2 * 1024 * 1024;
    if (uiInspection) uiInspection.hidden = false;
    if (fileName) fileName.textContent = file.name;
    if (fileSize) fileSize.textContent = `${(file.size / 1024).toFixed(file.size < 1024 * 1024 ? 1 : 0)} KiB`;
    if (uiClearFile) uiClearFile.disabled = false;
    if (!extensionValid || !sizeValid) {
      if (status) status.textContent = !extensionValid ? "Sai định dạng" : "Vượt quá 2 MiB";
      if (fileHash) fileHash.textContent = "Không tính";
      if (uiStagePack) uiStagePack.disabled = true;
      toast(!extensionValid ? "UI Pack phải là file .vtp hoặc .bin." : "UI Pack không được vượt quá 2 MiB.");
      return;
    }
    if (status) status.textContent = "Đang inspect…";
    if (fileHash) fileHash.textContent = "Đang tính…";
    try {
      const subtle = globalThis.crypto?.subtle;
      if (!subtle) throw new Error("secure-context-required");
      const digest = await subtle.digest("SHA-256", await file.arrayBuffer());
      const hash = [...new Uint8Array(digest)]
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("");
      if (fileHash) {
        fileHash.textContent = `${hash.slice(0, 12)}…${hash.slice(-8)}`;
        fileHash.title = hash;
      }
    } catch {
      if (fileHash) fileHash.textContent = "Tính khi staging";
    }
    if (status) status.textContent = "Hợp lệ để staging";
    selectedUiPack = file;
    stagedUiArtifact = undefined;
    uiPackAction = "stage";
    if (uiStagePack) {
      uiStagePack.disabled = false;
      uiStagePack.textContent = "Đưa vào staging";
    }
  };

  listen(uiPackInput, "change", (async () => {
    const file = uiPackInput?.files?.[0];
    if (file) await inspectUiPack(file);
  }) as EventListener);
  listen(uiDropZone, "dragover", ((event: Event) => {
    event.preventDefault();
    uiDropZone?.classList.add("dragging");
  }) as EventListener);
  listen(uiDropZone, "dragleave", (() => uiDropZone?.classList.remove("dragging")) as EventListener);
  listen(uiDropZone, "drop", (async (event: Event) => {
    event.preventDefault();
    uiDropZone?.classList.remove("dragging");
    const file = (event as DragEvent).dataTransfer?.files[0];
    if (file) await inspectUiPack(file);
  }) as EventListener);
  renderDeviceUiPreview();

  const codeInputs = queryAll<HTMLInputElement>(root, "#codeInputs input");
  codeInputs.forEach((input, index) => {
    listen(input, "input", (() => {
      input.value = input.value.replace(/\D/g, "").slice(0, 1);
      if (input.value) codeInputs[index + 1]?.focus();
      const submit = query<HTMLButtonElement>(root, "#pairSubmit");
      if (submit) submit.disabled = codeInputs.some((item) => !item.value);
    }) as EventListener);
    listen(input, "keydown", ((event: KeyboardEvent) => {
      if (event.key === "Backspace" && !input.value) codeInputs[index - 1]?.focus();
      if (event.key === "Enter") query<HTMLButtonElement>(root, "#pairSubmit")?.click();
    }) as EventListener);
  });

  listen(root, "submit", (async (event: Event) => {
    const form = event.target as HTMLFormElement;
    if (form.matches(".provider-edit-form")) {
      event.preventDefault();
      const values = new FormData(form);
      const providerId = values.get("providerId")?.toString() ?? "";
      const adapter = values.get("adapter")?.toString().trim() ?? "";
      const model = values.get("model")?.toString().trim() ?? "";
      const baseUrl = values.get("baseUrl")?.toString().trim() ?? "";
      const priority = Number(values.get("priority"));
      const locales = (values.get("locales")?.toString() ?? "")
        .split(",")
        .map((locale) => locale.trim())
        .filter(Boolean);
      const secretAction = (values.get("secretAction")?.toString() ??
        "keep") as ProviderUpdateInput["secretAction"];
      const secret = values.get("secret")?.toString() ?? "";
      if (!providerId || !adapter || !model || !locales.length || !Number.isInteger(priority)) {
        return toast("Provider cần adapter, model, priority và locale hợp lệ.");
      }
      if (secretAction === "rotate" && !secret) {
        return toast("Hãy nhập secret mới trước khi luân chuyển.");
      }
      const submit = query<HTMLButtonElement>(form, 'button[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        await callbacks.updateProvider(providerId, {
          adapter,
          model,
          baseUrl: baseUrl || null,
          enabled: values.get("enabled") === "true",
          priority,
          locales,
          secretAction,
          ...(secretAction === "rotate" ? { secret } : {}),
        });
        closeProvider();
        toast("Provider đã cập nhật; health và circuit được reset để probe lại.");
      } catch (error) {
        toast(error instanceof Error ? error.message : "Không thể cập nhật provider.");
      } finally {
        if (submit) submit.disabled = false;
      }
      return;
    }
    if (form.matches(".artifact-register-form")) {
      event.preventDefault();
      const artifactId = new FormData(form).get("artifactId")?.toString().trim() ?? "";
      const license = new FormData(form).get("license")?.toString().trim() ?? "";
      if (!artifactId || !license) return toast("Artifact ID và license không được để trống.");
      const submit = query<HTMLButtonElement>(form, 'button[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        await callbacks.registerArtifact(artifactId, license);
        toast("Artifact đã pass hash, signature, ABI và được thêm vào catalog.");
      } catch (error) {
        toast(error instanceof Error ? error.message : "Không thể đăng ký artifact.");
      } finally {
        if (submit) submit.disabled = false;
      }
      return;
    }
    if (form.matches(".wake-profile-form")) {
      event.preventDefault();
      const values = new FormData(form);
      const artifactId = values.get("artifactId")?.toString() ?? "";
      const name = values.get("name")?.toString().trim() ?? "";
      const activationPhrase = values.get("activationPhrase")?.toString().trim() ?? "";
      const channel = values.get("channel")?.toString() ?? "development";
      const activationDetectorId =
        values.get("activationDetectorId")?.toString().trim() ?? "";
      const interruptDetectorId =
        values.get("interruptDetectorId")?.toString().trim() ?? "";
      if (!artifactId || !name || !activationPhrase || !activationDetectorId || !interruptDetectorId) {
        return toast("Hãy nhập đủ artifact, phrase và detector ID.");
      }
      const submit = query<HTMLButtonElement>(form, 'button[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        await callbacks.createWakeProfile({
          artifactId,
          name,
          locale: "vi-VN",
          channel,
          activationPhrase,
          activation: {
            detectorId: activationDetectorId,
            sensitivity: 0.5,
            cooldownMs: 1_500,
            allowedStates: ["standby"],
          },
          interrupt: {
            detectorId: interruptDetectorId,
            sensitivity: 0.6,
            cooldownMs: 800,
            allowedStates: ["thinking", "speaking"],
          },
        });
        toast("Đã tạo wake profile draft; phrase chưa đồng nghĩa model đã benchmark.");
      } catch (error) {
        toast(error instanceof Error ? error.message : "Không thể tạo wake profile.");
      } finally {
        if (submit) submit.disabled = false;
      }
    }
  }) as EventListener);

  listen(root, "click", (async (event: Event) => {
    const target = event.target as Element;
    const page = target.closest<HTMLElement>("[data-page-link]")?.dataset.pageLink;
    if (page) {
      event.preventDefault();
      showPage(page);
      return;
    }
    const uiState = target.closest<HTMLButtonElement>("[data-ui-state]")?.dataset.uiState;
    if (uiState && uiState in deviceUiCopy.vi) {
      deviceUiState = uiState as DeviceUiState;
      renderDeviceUiPreview();
      return;
    }
    const uiTheme = target.closest<HTMLButtonElement>("[data-ui-theme]")?.dataset.uiTheme;
    if (uiTheme && uiTheme in deviceUiThemeNames) {
      deviceUiTheme = uiTheme as DeviceUiTheme;
      renderDeviceUiPreview();
      return;
    }
    if (target.closest("[data-ui-language]")) {
      deviceUiLocale = deviceUiLocale === "vi" ? "en" : "vi";
      const toggle = query<HTMLButtonElement>(root, "[data-ui-language]");
      if (toggle) {
        toggle.innerHTML = deviceUiLocale === "vi" ? 'VI <span>/ EN</span>' : '<span>VI /</span> EN';
      }
      renderDeviceUiPreview();
      return;
    }
    if (target.closest("[data-open-ui-upload]")) {
      query<HTMLElement>(root, "#uiUploadPanel")?.scrollIntoView({ behavior: "smooth", block: "center" });
      uiPackInput?.click();
      return;
    }
    if (target.closest("[data-ui-clear-file]")) {
      clearUiPack();
      return;
    }
    if (target.closest("[data-ui-stage-pack]")) {
      if (!uiStagePack || !selectedUiPack) return;
      uiStagePack.disabled = true;
      const status = query<HTMLElement>(root, "[data-ui-upload-status]");
      try {
        if (uiPackAction === "stage") {
          if (status) status.textContent = "Đang upload & kiểm tra…";
          stagedUiArtifact = await callbacks.stageUiPack(selectedUiPack);
          uiPackAction = "publish";
          uiStagePack.textContent = "Publish UI Pack";
          if (status) status.textContent = `Staging · ${stagedUiArtifact.id}`;
          toast("UI Pack đã được kiểm container, ký và đưa vào catalog immutable.");
        } else if (uiPackAction === "publish" && stagedUiArtifact) {
          await callbacks.publishArtifact(stagedUiArtifact.id);
          stagedUiArtifact = { ...stagedUiArtifact, status: "published" };
          uiPackAction = "rollout";
          uiStagePack.textContent = "Rollout lên thiết bị";
          if (status) status.textContent = "Published · chờ rollout";
          toast("UI Pack đã publish; thiết bị chưa đổi cho tới khi rollout.");
        } else if (uiPackAction === "rollout" && stagedUiArtifact) {
          await callbacks.rolloutUiPack(stagedUiArtifact.id);
          if (status) status.textContent = "Rollout · chờ thiết bị apply";
          uiStagePack.textContent = "Đã tạo rollout";
          toast("Desired UI đã cập nhật; chờ ESP32 verify, activate và report.");
          return;
        }
      } catch (error) {
        toast(error instanceof Error ? error.message : "Không thể xử lý UI Pack.");
      } finally {
        uiStagePack.disabled = uiPackAction === "rollout" && uiStagePack.textContent === "Đã tạo rollout";
      }
      return;
    }
    if (target.closest("[data-open-pair]")) return openPairing();
    if (target.closest("[data-close-modal]")) return closePairing();
    if (target.closest("[data-close-provider-modal]")) return closeProvider();
    if (target === query(root, "#pairModal")) return closePairing();
    if (target === query(root, "#providerModal")) return closeProvider();
    const publishArtifact = target.closest<HTMLButtonElement>(".publish-artifact");
    if (publishArtifact?.dataset.artifactId) {
      publishArtifact.disabled = true;
      try {
        await callbacks.publishArtifact(publishArtifact.dataset.artifactId);
        toast("Artifact immutable đã được publish; chưa có nghĩa thiết bị đã apply.");
      } catch (error) {
        toast(error instanceof Error ? error.message : "Không thể publish artifact.");
      }
      return;
    }
    const publishWake = target.closest<HTMLButtonElement>(".publish-wake-profile");
    if (publishWake?.dataset.wakeProfileId) {
      publishWake.disabled = true;
      try {
        await callbacks.publishWakeProfile(publishWake.dataset.wakeProfileId);
        toast("Wake profile version immutable đã được publish.");
      } catch (error) {
        toast(error instanceof Error ? error.message : "Không thể publish wake profile.");
      }
      return;
    }
    const rolloutWake = target.closest<HTMLButtonElement>(".rollout-wake-profile");
    if (rolloutWake?.dataset.wakeProfileId && rolloutWake.dataset.deviceId) {
      rolloutWake.disabled = true;
      try {
        await callbacks.rolloutWakeProfile(rolloutWake.dataset.wakeProfileId, [
          rolloutWake.dataset.deviceId,
        ]);
        toast("Đã tạo desired rollout; chờ reported state xác nhận active.");
      } catch (error) {
        toast(error instanceof Error ? error.message : "Rollout bị từ chối.");
      }
      return;
    }
    const toolItem = target.closest<HTMLButtonElement>(".tool-item[data-tool-name]");
    if (toolItem?.dataset.toolName) {
      const catalog = toolCatalogs.get(root);
      const tool = catalog?.tools.find((item) => item.name === toolItem.dataset.toolName);
      queryAll(root, ".tool-item").forEach((item) => item.classList.remove("active"));
      toolItem.classList.add("active");
      renderToolDetail(root, tool, catalog?.activeDeviceId, catalog?.live ?? false);
      return;
    }
    const runTool = target.closest<HTMLButtonElement>(".run-device-tool");
    if (runTool) {
      const form = runTool.closest<HTMLFormElement>(".tool-call-form");
      const deviceId = form?.dataset.deviceId;
      const toolName = form?.dataset.toolName;
      if (!form || !deviceId || !toolName || !form.reportValidity()) return;
      const argumentsValue: Record<string, unknown> = {};
      try {
        queryAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
          form,
          "[data-tool-argument]",
        ).forEach((input) => {
          const name = input.dataset.toolArgument;
          if (!name) return;
          const kind = input.dataset.argumentKind;
          if (kind === "boolean" && input instanceof HTMLInputElement) {
            argumentsValue[name] = input.checked;
          } else if (kind === "number") {
            if (input.value !== "") argumentsValue[name] = Number(input.value);
          } else if (kind === "json") {
            if (input.value !== "") argumentsValue[name] = JSON.parse(input.value);
          } else if (input.value !== "") {
            argumentsValue[name] = input.value;
          }
        });
      } catch {
        return toast("Tham số JSON không hợp lệ.");
      }
      const confirmed =
        query<HTMLInputElement>(form, "[data-tool-confirmed]")?.checked ?? false;
      runTool.disabled = true;
      const previous = runTool.textContent;
      runTool.textContent = "Đang thực thi…";
      try {
        const result = await callbacks.callTool(
          deviceId,
          toolName,
          argumentsValue,
          confirmed,
        );
        const summary = JSON.stringify(result);
        toast(`MCP thành công · ${summary.slice(0, 180)}`);
      } catch (error) {
        toast(error instanceof Error ? error.message : "MCP call thất bại.");
      } finally {
        runTool.disabled = false;
        runTool.textContent = previous;
      }
      return;
    }
    const testButton = target.closest<HTMLButtonElement>(".test-provider");
    if (testButton?.dataset.providerId) {
      testButton.disabled = true;
      const previous = testButton.textContent;
      testButton.textContent = "…";
      try {
        await callbacks.testProvider(testButton.dataset.providerId);
        toast("Provider health check đã hoàn tất.");
      } catch (error) {
        toast(error instanceof Error ? error.message : "Không thể test provider.");
      } finally {
        testButton.disabled = false;
        testButton.textContent = previous;
      }
      return;
    }
    const editButton = target.closest<HTMLButtonElement>(".edit-provider");
    if (editButton?.dataset.providerId) {
      const provider = providerCatalogs
        .get(root)
        ?.find((item) => item.id === editButton.dataset.providerId);
      if (provider) openProvider(provider);
      return;
    }
    if (target.closest(".publish-agent")) {
      const panel = query<HTMLElement>(root, ".config-panel");
      const id = panel?.dataset.agentId;
      if (!panel || !id) return;
      const name = query<HTMLInputElement>(panel, '[data-agent-field="name"]')?.value.trim() ?? "";
      const persona = query<HTMLTextAreaElement>(panel, '[data-agent-field="persona"]')?.value.trim() ?? "";
      const defaultLocale = query<HTMLSelectElement>(panel, '[data-agent-field="locale"]')?.value ?? "vi-VN";
      const interactionMode = (query<HTMLSelectElement>(panel, '[data-agent-field="mode"]')?.value ?? "auto") as Agent["interactionMode"];
      const firstInputSeconds = Number(
        query<HTMLInputElement>(panel, '[data-agent-field="first-input"]')?.value ?? 15,
      );
      const betweenTurnsSeconds = Number(
        query<HTMLInputElement>(panel, '[data-agent-field="between-turns"]')?.value ?? 30,
      );
      const closingGraceSeconds = Number(
        query<HTMLInputElement>(panel, '[data-agent-field="closing-grace"]')?.value ?? 5,
      );
      const maxSessionSeconds = Number(
        query<HTMLInputElement>(panel, '[data-agent-field="max-session"]')?.value ?? 600,
      );
      const timeoutGoodbye =
        query<HTMLInputElement>(panel, '[data-agent-field="timeout-goodbye"]')?.value.trim() ??
        "";
      const providerChains = ["vad", "asr", "llm", "tts"].map((kind) => {
        const primary = query<HTMLSelectElement>(
          panel,
          `[data-provider-chain-kind="${kind}"][data-provider-chain-role="primary"]`,
        )?.value;
        const fallback = query<HTMLSelectElement>(
          panel,
          `[data-provider-chain-kind="${kind}"][data-provider-chain-role="fallback"]`,
        )?.value;
        return {
          kind,
          locale: defaultLocale,
          providerIds: [primary, fallback].filter((value): value is string => Boolean(value)),
        };
      });
      if (!name || !persona) return toast("Tên và system prompt không được để trống.");
      if (!timeoutGoodbye) return toast("Lời chào khi timeout không được để trống.");
      try {
        await callbacks.publishAgent({
          id,
          name,
          persona,
          defaultLocale,
          interactionMode,
          draftConfig: {
            providerChains,
            conversation: {
              firstInputSeconds,
              betweenTurnsSeconds,
              closingGraceSeconds,
              maxSessionSeconds,
              timeoutGoodbye,
            },
          },
        });
        toast("Đã publish immutable agent config mới.");
      } catch (error) {
        toast(error instanceof Error ? error.message : "Không thể publish agent.");
      }
      return;
    }
    if (target.closest(".profile-card")) {
      await callbacks.logout();
    }
  }) as EventListener);

  listen(query(root, "#pairSubmit"), "click", (async () => {
    const submit = query<HTMLButtonElement>(root, "#pairSubmit");
    const code = codeInputs.map((input) => input.value).join("");
    const agentId = query<HTMLSelectElement>(root, "#pairModal select")?.value || undefined;
    if (!submit || !/^\d{6}$/.test(code)) return;
    submit.disabled = true;
    submit.textContent = "Đang ghép…";
    try {
      await callbacks.pair(code, `Veetee ${code.slice(-2)}`, agentId);
      codeInputs.forEach((input) => {
        input.value = "";
      });
      closePairing();
      toast("Đã ghép thiết bị và tạo desired state ban đầu.");
    } catch (error) {
      toast(error instanceof Error ? error.message : "Không thể ghép thiết bị.");
    } finally {
      submit.textContent = "Ghép thiết bị";
      submit.disabled = codeInputs.some((input) => !input.value);
    }
  }) as EventListener);

  const palette = query<HTMLElement>(root, "#commandPalette");
  const commandInput = query<HTMLInputElement>(root, "#commandInput");
  const closePalette = (): void => {
    if (palette) palette.hidden = true;
    if (commandInput) commandInput.value = "";
  };
  listen(query(root, "#commandTrigger"), "click", (() => {
    if (palette) palette.hidden = false;
    window.setTimeout(() => commandInput?.focus(), 50);
  }) as EventListener);
  listen(document, "keydown", ((event: KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (palette) palette.hidden = false;
      window.setTimeout(() => commandInput?.focus(), 50);
    }
    if (event.key === "Escape") {
      closePalette();
      closePairing();
      closeProvider();
    }
  }) as EventListener);
  listen(palette, "click", ((event: MouseEvent) => {
    const target = event.target as Element;
    if (target === palette) closePalette();
    const page = target.closest<HTMLElement>("[data-command-page]")?.dataset.commandPage;
    if (page) {
      closePalette();
      showPage(page);
    }
    if (target.closest("[data-command-pair]")) {
      closePalette();
      openPairing();
    }
  }) as EventListener);

  const labToggle = query<HTMLButtonElement>(root, "#labToggle");
  listen(labToggle, "click", (() => {
    toast("Realtime Lab tự đồng bộ mỗi 1,5 giây; hãy gọi robot bằng nút hoặc wake word.");
  }) as EventListener);
  listen(query(root, "#interruptButton"), "click", (() => {
    toast("Remote interrupt chưa được bật; hãy dùng nút vật lý trên robot.");
  }) as EventListener);

  showPage(location.hash.slice(1) || "overview");
  return {
    toast,
    closePairing,
    destroy(): void {
      abort.abort();
      window.clearTimeout(toastTimer);
    },
  };
}
