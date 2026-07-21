import type { Agent, Device, McpTool, Principal, Provider } from "./api/schemas";

const pageNames: Record<string, string> = {
  overview: "Tổng quan",
  devices: "Thiết bị",
  agents: "Trợ lý AI",
  providers: "Providers",
  lab: "Realtime Lab",
  mcp: "MCP tools",
  ota: "OTA & releases",
};

export interface AgentDraftInput {
  id: string;
  name: string;
  defaultLocale: string;
  interactionMode: Agent["interactionMode"];
  persona: string;
  draftConfig: Record<string, unknown>;
}

interface PrototypeCallbacks {
  pair(code: string, name: string, agentId?: string): Promise<void>;
  testProvider(providerId: string): Promise<void>;
  publishAgent(input: AgentDraftInput): Promise<void>;
  logout(): Promise<void>;
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
              <button class="button button-ghost" type="button">Mở thiết bị →</button></div></article>`;
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

function renderAgents(root: HTMLElement, agents: Agent[]): void {
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
  const betweenTurns = Number(conversation.betweenTurnsSeconds ?? 30);
  poster.innerHTML = `<span class="poster-tag">PUBLISHED · V${agent.publishedVersion}</span><div class="poster-face"><i></i><i></i><b>⌣</b></div><h2>${escapeHtml(agent.name)}</h2><p>${escapeHtml(agent.persona)}</p><div class="poster-meta"><span>${escapeHtml(agent.defaultLocale)}</span><span>${escapeHtml(agent.interactionMode)}</span><span>version ${agent.publishedVersion}</span></div>`;
  panel.dataset.agentId = agent.id;
  panel.innerHTML = `<div class="tabs"><button class="active">Hành vi</button><button>Providers</button><button>Ngôn ngữ</button><button>Tools</button></div>
    <label>Tên trợ lý<input data-agent-field="name" value="${escapeHtml(agent.name)}"></label>
    <label>System prompt<textarea data-agent-field="persona">${escapeHtml(agent.persona)}</textarea></label>
    <div class="two-cols"><label>Locale mặc định<select data-agent-field="locale"><option value="vi-VN" ${agent.defaultLocale === "vi-VN" ? "selected" : ""}>Tiếng Việt (vi-VN)</option><option value="en-US" ${agent.defaultLocale === "en-US" ? "selected" : ""}>English (en-US)</option></select></label>
    <label>Chế độ hội thoại<select data-agent-field="mode"><option value="auto" ${agent.interactionMode === "auto" ? "selected" : ""}>Cascade realtime · auto</option><option value="realtime" ${agent.interactionMode === "realtime" ? "selected" : ""}>End-to-end realtime · auto</option><option value="manual" ${agent.interactionMode === "manual" ? "selected" : ""}>Manual/PTT compatibility</option></select></label></div>
    <div class="two-cols"><label>Không có lượt mới<select data-agent-field="timeout"><option value="30" ${betweenTurns === 30 ? "selected" : ""}>30 giây → chào và ngủ</option><option value="60" ${betweenTurns === 60 ? "selected" : ""}>60 giây → chào và ngủ</option></select></label><label>Input admission<select disabled><option>Semantic + quality gate</option></select></label></div>
    <div class="publish-bar"><div><span class="unsaved-dot"></span><p><b>Lưu draft và publish immutable version</b><small>Thiết bị đang dùng version ${agent.publishedVersion}</small></p></div><button class="button button-primary publish-agent" type="button">Publish version ${agent.version + 1}</button></div>`;

  const select = query<HTMLSelectElement>(root, "#pairModal select");
  if (select) {
    select.innerHTML = `${agents.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · v${item.publishedVersion}</option>`).join("")}<option value="">Chưa gán trợ lý</option>`;
  }
}

function renderProviders(root: HTMLElement, providers: Provider[]): void {
  const table = query<HTMLElement>(root, ".provider-table");
  if (table) {
    table.innerHTML = `<div class="provider-row table-head"><span>Provider</span><span>Loại</span><span>Locale / model</span><span>Secret</span><span>Health</span><span></span></div>${providers
      .map(
        (provider) => `<div class="provider-row"><span><i class="provider-logo local">${escapeHtml(provider.kind.slice(0, 2).toUpperCase())}</i><b>${escapeHtml(provider.adapter)}</b></span><span>${escapeHtml(provider.kind.toUpperCase())}</span><span>${escapeHtml(provider.model)}</span><span>${provider.secretConfigured ? "configured" : "local / none"}</span><span class="health-text ${provider.health === "healthy" ? "ok-text" : provider.health === "degraded" ? "warn-text" : ""}">${escapeHtml(provider.health)}</span><button class="test-provider" data-provider-id="${escapeHtml(provider.id)}" type="button">Test</button></div>`,
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

function renderTools(root: HTMLElement, tools: McpTool[]): void {
  const list = query<HTMLElement>(root, ".tool-list");
  const detail = query<HTMLElement>(root, ".tool-detail");
  if (!list || !detail) return;
  list.innerHTML = tools
    .map(
      (tool, index) => `<button class="tool-item ${index === 0 ? "active" : ""}" type="button"><span class="tool-type ${tool.audience === "regular" ? "safe" : "user"}">${tool.audience === "regular" ? "AI" : "USER"}</span><div><b>${escapeHtml(tool.name)}</b><small>${escapeHtml(tool.description)}</small></div><i>→</i></button>`,
    )
    .join("");
  const tool = tools[0];
  detail.innerHTML = tool
    ? `<span class="status-pill"><i></i> ${tool.audience === "regular" ? "AI-callable" : "User-only"}</span><h2>${escapeHtml(tool.name)}</h2><p>${escapeHtml(tool.description)}</p><div class="schema-block"><span>INPUT SCHEMA</span><pre>${escapeHtml(JSON.stringify(tool.inputSchema, null, 2))}</pre></div><button class="button button-primary" type="button">Chạy thử trên thiết bị</button><small class="audit-note">Mọi tool call đều được ghi audit.</small>`
    : '<div class="empty-state"><b>Chưa có MCP tool.</b></div>';
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
  const voiceStatus = query<HTMLElement>(root, ".voice-card .status-pill");
  if (voiceStatus) voiceStatus.innerHTML = "<i></i> Voice telemetry pending";
  const latency = query<HTMLElement>(root, ".voice-card .latency-row");
  if (latency) {
    latency.innerHTML = '<div><span>First audio</span><b>—</b><em>chờ phiên thật</em></div><div><span>Ngắt lời</span><b>—</b><em>chưa đo hardware</em></div><div><span>Phiên hôm nay</span><b>0</b><em>event ingestion chưa bật</em></div>';
  }
  const traceId = query<HTMLElement>(root, ".trace-id");
  const timeline = query<HTMLElement>(root, ".timeline");
  if (traceId) traceId.textContent = "NO LIVE TURN";
  if (timeline) {
    timeline.innerHTML = '<div class="empty-state"><b>Chưa có conversation event thật.</b><small>Realtime Lab sẽ hiển thị admission, ASR, MCP, cancellation và TTS khi event ingestion được bật.</small></div>';
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

function renderAvailability(root: HTMLElement, data: ManagerViewData): void {
  const device = data.devices[0];
  const agent = data.agents[0];
  const consoleTop = query<HTMLElement>(root, ".console-top");
  if (consoleTop) {
    consoleTop.innerHTML = `<div><span>DEVICE</span><b>${escapeHtml(device?.name ?? "Chưa có thiết bị")}</b></div><div><span>AGENT</span><b>${escapeHtml(agent ? `${agent.name} · v${agent.publishedVersion}` : "Chưa có agent")}</b></div><div><span>ENGINE</span><b>${escapeHtml(agent ? `${agent.interactionMode} · auto gate` : "Cascade · auto")}</b></div>`;
  }
  const labPrompt = query<HTMLElement>(root, "#labPrompt");
  if (labPrompt) {
    labPrompt.textContent =
      "Đây là mô phỏng UI. Live event ingestion sẽ được nối sau khi firmware tạo phiên thật.";
  }
  const releaseHero = query<HTMLElement>(root, ".release-hero");
  const releaseList = query<HTMLElement>(root, ".release-list");
  if (releaseHero) {
    releaseHero.innerHTML = '<div><span class="status-pill neutral"><i></i> CHƯA CÓ ARTIFACT</span><h2>OTA signing pipeline chưa được cấu hình</h2><p>Firmware chỉ xuất hiện ở đây sau khi artifact đã validate, ký và có manifest tương thích board veetee-s3-n16r8.</p><div class="release-meta"><span><small>Executable OTA</small><b>A/B required</b></span><span><small>Resource bundle</small><b>signed inactive slot</b></span><span><small>Rollout</small><b>canary first</b></span></div></div><div class="rollout-ring"><b>0%</b><span>0 thiết bị</span><small>rollout</small></div>';
  }
  if (releaseList) {
    releaseList.innerHTML = '<div class="empty-state"><b>Chưa có release thật.</b><small>UI không hiển thị firmware mẫu để tránh nhầm với artifact đã ký.</small></div>';
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
  const agentsSignature = JSON.stringify(data.agents);
  const providersSignature = JSON.stringify(data.providers);
  const toolsSignature = JSON.stringify(data.tools);
  if (signatures.devices !== devicesSignature) {
    renderDevices(root, data.devices, data.agents);
    signatures.devices = devicesSignature;
  }
  if (signatures.agents !== agentsSignature) {
    renderAgents(root, data.agents);
    signatures.agents = agentsSignature;
  }
  if (signatures.providers !== providersSignature) {
    renderProviders(root, data.providers);
    signatures.providers = providersSignature;
  }
  if (signatures.tools !== toolsSignature) {
    renderTools(root, data.tools);
    signatures.tools = toolsSignature;
  }
  renderSignatures.set(root, signatures);
  renderOverview(root, data);
  renderAvailability(root, data);
}

export function initializePrototype(
  root: HTMLElement,
  callbacks: PrototypeCallbacks,
): PrototypeController {
  const abort = new AbortController();
  const timers = new Set<number>();
  const signal = abort.signal;
  let toastTimer = 0;
  let labRunning = false;

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

  listen(root, "click", (async (event: Event) => {
    const target = event.target as Element;
    const page = target.closest<HTMLElement>("[data-page-link]")?.dataset.pageLink;
    if (page) {
      event.preventDefault();
      showPage(page);
      return;
    }
    if (target.closest("[data-open-pair]")) return openPairing();
    if (target.closest("[data-close-modal]")) return closePairing();
    if (target === query(root, "#pairModal")) return closePairing();
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
    if (target.closest(".publish-agent")) {
      const panel = query<HTMLElement>(root, ".config-panel");
      const id = panel?.dataset.agentId;
      if (!panel || !id) return;
      const name = query<HTMLInputElement>(panel, '[data-agent-field="name"]')?.value.trim() ?? "";
      const persona = query<HTMLTextAreaElement>(panel, '[data-agent-field="persona"]')?.value.trim() ?? "";
      const defaultLocale = query<HTMLSelectElement>(panel, '[data-agent-field="locale"]')?.value ?? "vi-VN";
      const interactionMode = (query<HTMLSelectElement>(panel, '[data-agent-field="mode"]')?.value ?? "auto") as Agent["interactionMode"];
      const betweenTurnsSeconds = Number(query<HTMLSelectElement>(panel, '[data-agent-field="timeout"]')?.value ?? 30);
      if (!name || !persona) return toast("Tên và system prompt không được để trống.");
      try {
        await callbacks.publishAgent({
          id,
          name,
          persona,
          defaultLocale,
          interactionMode,
          draftConfig: { conversation: { betweenTurnsSeconds } },
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

  const labState = query<HTMLElement>(root, "#labState");
  const labToggle = query<HTMLButtonElement>(root, "#labToggle");
  const labPrompt = query<HTMLElement>(root, "#labPrompt");
  const eventLog = query<HTMLElement>(root, "#eventLog");
  const stopLab = (message: string): void => {
    timers.forEach((timer) => window.clearTimeout(timer));
    timers.clear();
    labRunning = false;
    labState?.classList.remove("running");
    if (labState) labState.innerHTML = "<i></i> Sẵn sàng";
    if (labToggle) labToggle.textContent = "Bắt đầu phiên thử";
    if (labPrompt) labPrompt.textContent = message;
    query(root, "#labOrb")?.classList.remove("running");
  };
  listen(labToggle, "click", (() => {
    if (labRunning) return stopLab("Đã dừng phiên mô phỏng.");
    labRunning = true;
    labState?.classList.add("running");
    if (labState) labState.innerHTML = "<i></i> Đang chạy";
    if (labToggle) labToggle.textContent = "Dừng phiên";
    if (labPrompt) labPrompt.textContent = "Đang mô phỏng luồng auto: hãy nói một câu tiếng Việt.";
    if (eventLog) eventLog.innerHTML = "";
    const events = [
      ["listen:start", "Assistant gate mở · mode=auto", "0 ms"],
      ["input.accepted", "Input đủ tin cậy và hướng tới robot", "188 ms"],
      ["vad.final", "Tự kết thúc lượt nói · không cần click lần hai", "188 ms"],
      ["stt", "ASR final tiếng Việt", "312 ms"],
      ["llm.first_token", "9Router bắt đầu stream", "302 ms"],
      ["tts.first_audio", "VieNeu local", "535 ms"],
    ];
    events.forEach(([name, detail, time], index) => {
      const timer = window.setTimeout(() => {
        eventLog?.insertAdjacentHTML(
          "beforeend",
          `<div class="event-entry"><i></i><div><b>${name}</b><small>${detail}</small></div><em>${time}</em></div>`,
        );
      }, 450 + index * 620);
      timers.add(timer);
    });
  }) as EventListener);
  listen(query(root, "#interruptButton"), "click", (() => {
    if (!labRunning) return toast("Chưa có turn đang chạy.");
    stopLab("Đã ngắt AI · generation cũ bị vô hiệu hóa, sẵn sàng nghe turn mới.");
    eventLog?.insertAdjacentHTML(
      "beforeend",
      '<div class="event-entry"><i></i><div><b>abort</b><small>User interrupt · generation invalidated</small></div><em>118 ms</em></div>',
    );
  }) as EventListener);

  showPage(location.hash.slice(1) || "overview");
  return {
    toast,
    closePairing,
    destroy(): void {
      abort.abort();
      timers.forEach((timer) => window.clearTimeout(timer));
      window.clearTimeout(toastTimer);
    },
  };
}
