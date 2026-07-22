import type { Agent, Device, LabSession as IssuedLabSession } from "./api/schemas";

type InputMode = IssuedLabSession["inputMode"];
type McpMode = IssuedLabSession["mcpMode"];

interface LabEvent {
  type: "lab.event";
  session_id: string;
  event: string;
  elapsed_ms: number;
  generation: number;
  turn_id?: string;
  payload: Record<string, unknown>;
}

interface LabHello {
  type: "lab.hello";
  session_id: string;
  input_mode: InputMode;
  mcp_mode: McpMode;
  audio: { output_sample_rate: number };
  fidelity: Record<string, string>;
}

interface LabCallbacks {
  createSession(input: {
    agentId: string;
    inputMode: InputMode;
    mcpMode: McpMode;
    deviceId?: string;
  }): Promise<IssuedLabSession>;
  toast(message: string): void;
}

export interface RealtimeLabController {
  updateCatalog(agents: Agent[], devices: Device[]): void;
  destroy(): void;
}

function element<T extends Element>(root: ParentNode, selector: string): T {
  const result = root.querySelector<T>(selector);
  if (!result) throw new Error(`Realtime Lab element is missing: ${selector}`);
  return result;
}

function escapeHtml(value: unknown): string {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function lastIndexOf(events: LabEvent[], name: string): number {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index]?.event === name) return index;
  }
  return -1;
}

function lastEvent(events: LabEvent[], name: string): LabEvent | undefined {
  const index = lastIndexOf(events, name);
  return index >= 0 ? events[index] : undefined;
}

class StreamingResampler {
  private samples = new Float32Array(0);
  private position = 0;

  constructor(
    private readonly sourceRate: number,
    private readonly targetRate: number,
  ) {}

  push(input: Float32Array): Float32Array {
    const joined = new Float32Array(this.samples.length + input.length);
    joined.set(this.samples);
    joined.set(input, this.samples.length);
    const ratio = this.sourceRate / this.targetRate;
    const output: number[] = [];
    while (this.position + 1 < joined.length) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      output.push(joined[index]! * (1 - fraction) + joined[index + 1]! * fraction);
      this.position += ratio;
    }
    const consumed = Math.floor(this.position);
    this.samples = joined.slice(consumed);
    this.position -= consumed;
    return Float32Array.from(output);
  }
}

export function initializeRealtimeLab(
  root: HTMLElement,
  callbacks: LabCallbacks,
): RealtimeLabController {
  const section = element<HTMLElement>(root, '[data-page="lab"]');
  section.dataset.labManaged = "true";
  const setup = element<HTMLFormElement>(root, "#labSetup");
  const agentSelect = element<HTMLSelectElement>(root, "#labAgent");
  const inputModeSelect = element<HTMLSelectElement>(root, "#labInputMode");
  const mcpModeSelect = element<HTMLSelectElement>(root, "#labMcpMode");
  const deviceField = element<HTMLElement>(root, "#labDeviceField");
  const deviceSelect = element<HTMLSelectElement>(root, "#labDevice");
  const startButton = element<HTMLButtonElement>(root, "#labToggle");
  const endButton = element<HTMLButtonElement>(root, "#labEndButton");
  const interruptButton = element<HTMLButtonElement>(root, "#interruptButton");
  const wakeButton = element<HTMLButtonElement>(root, "#labWakeButton");
  const rawToggle = element<HTMLButtonElement>(root, "#labRawToggle");
  const stateLabel = element<HTMLElement>(root, "#labState");
  const prompt = element<HTMLElement>(root, "#labPrompt");
  const orb = element<HTMLElement>(root, "#labOrb");
  const chat = element<HTMLElement>(root, "#labChat");
  const eventLog = element<HTMLElement>(root, "#eventLog");
  const rawEvents = element<HTMLElement>(root, "#labRawEvents");
  const metrics = element<HTMLElement>(root, "#labMetrics");
  const sessionIdLabel = element<HTMLElement>(root, "#labSessionId");
  const deviceLabel = element<HTMLElement>(root, "#labDeviceLabel");
  const agentLabel = element<HTMLElement>(root, "#labAgentLabel");
  const engineLabel = element<HTMLElement>(root, "#labEngineLabel");
  const textForm = element<HTMLFormElement>(root, "#labTextForm");
  const textInput = element<HTMLInputElement>(root, "#labTextInput");
  const textSubmit = element<HTMLButtonElement>(textForm, 'button[type="submit"]');
  const replayPanel = element<HTMLElement>(root, "#labAudioReplay");
  const audioFile = element<HTMLInputElement>(root, "#labAudioFile");
  const replayButton = element<HTMLButtonElement>(root, "#labReplayButton");
  const audioMeta = element<HTMLElement>(root, "#labAudioMeta");
  const micPanel = element<HTMLElement>(root, "#labLiveMic");
  const micButton = element<HTMLButtonElement>(root, "#labMicButton");
  const micHint = element<HTMLElement>(root, "#labMicHint");
  const abort = new AbortController();

  let agents: Agent[] = [];
  let devices: Device[] = [];
  let socket: WebSocket | undefined;
  let issued: IssuedLabSession | undefined;
  let sessionId = "";
  let connected = false;
  let listening = false;
  let events: LabEvent[] = [];
  let outputSampleRate = 24_000;
  let audioContext: AudioContext | undefined;
  let nextPlaybackAt = 0;
  const playbackSources = new Set<AudioBufferSourceNode>();
  let currentAssistantMessage: HTMLElement | undefined;
  let audioSendGeneration = 0;
  let micStream: MediaStream | undefined;
  let micSource: MediaStreamAudioSourceNode | undefined;
  let micNode: AudioWorkletNode | undefined;
  let micSilentGain: GainNode | undefined;
  let micPcm = new Int16Array(0);

  const selectedInputMode = (): InputMode => inputModeSelect.value as InputMode;
  const selectedMcpMode = (): McpMode => mcpModeSelect.value as McpMode;

  const setState = (label: string, status: "idle" | "running" | "error" = "idle"): void => {
    stateLabel.classList.toggle("running", status === "running");
    stateLabel.classList.toggle("error", status === "error");
    stateLabel.innerHTML = `<i></i> ${escapeHtml(label)}`;
  };

  const send = (payload: Record<string, unknown>): boolean => {
    if (!socket || socket.readyState !== WebSocket.OPEN || !sessionId) return false;
    socket.send(JSON.stringify({ ...payload, session_id: sessionId }));
    return true;
  };

  const updateInputPanels = (): void => {
    const mode = selectedInputMode();
    textForm.hidden = mode !== "text";
    replayPanel.hidden = mode !== "audio_replay";
    micPanel.hidden = mode !== "live_mic";
    deviceField.hidden = selectedMcpMode() !== "selected_device";
    const available = connected && listening;
    textInput.disabled = !available || mode !== "text";
    textSubmit.disabled = textInput.disabled;
    replayButton.disabled = !available || mode !== "audio_replay" || !audioFile.files?.[0];
    micButton.disabled = !connected || mode !== "live_mic";
  };

  const lockSetup = (locked: boolean): void => {
    for (const select of [agentSelect, inputModeSelect, mcpModeSelect, deviceSelect]) {
      select.disabled = locked;
    }
    startButton.disabled = locked;
    endButton.disabled = !locked;
    interruptButton.disabled = !locked;
    setup.classList.toggle("locked", locked);
  };

  const appendMessage = (kind: "user" | "assistant" | "system", text: string): HTMLElement => {
    const message = document.createElement("div");
    message.className = `lab-message ${kind}`;
    message.textContent = text;
    chat.append(message);
    chat.scrollTop = chat.scrollHeight;
    return message;
  };

  const stopPlayback = (): void => {
    for (const source of playbackSources) {
      try {
        source.stop();
      } catch {
        // A source may already have ended between the click and this loop.
      }
    }
    playbackSources.clear();
    nextPlaybackAt = audioContext?.currentTime ?? 0;
  };

  const ensureAudioContext = async (): Promise<AudioContext> => {
    audioContext ??= new AudioContext({ latencyHint: "interactive" });
    if (audioContext.state === "suspended") await audioContext.resume();
    return audioContext;
  };

  const playPcm = async (data: ArrayBuffer): Promise<void> => {
    const context = await ensureAudioContext();
    const samples = new Int16Array(data);
    if (!samples.length) return;
    const buffer = context.createBuffer(1, samples.length, outputSampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) {
      channel[index] = samples[index]! / 32768;
    }
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.015, nextPlaybackAt);
    nextPlaybackAt = startAt + buffer.duration;
    playbackSources.add(source);
    source.onended = () => playbackSources.delete(source);
    source.start(startAt);
  };

  const eventDetails = (event: LabEvent): string => {
    const payload = event.payload;
    if (event.event === "stt.final") {
      return `${String(payload.source ?? "audio")} · ${String(payload.text ?? "")}`;
    }
    if (event.event === "admission.final") {
      return `${String(payload.disposition ?? "unknown")} · ${String(payload.reason_code ?? "")}`;
    }
    if (event.event === "planner.final") {
      return `${String(payload.action ?? "unknown")} · ${String(payload.intent ?? "")}`;
    }
    if (event.event.startsWith("mcp.")) {
      return `${String(payload.tool ?? "tool")} · ${String(payload.duration_ms ?? "…")} ms`;
    }
    if (event.event === "abort.complete") {
      return `${String(payload.reason ?? "interrupt")} · ${String(payload.duration_ms ?? "…")} ms`;
    }
    if (event.event.includes("bypassed")) return String(payload.reason ?? "bypassed");
    if (event.event === "turn.error") return String(payload.code ?? payload.error_type ?? "error");
    return Object.entries(payload)
      .slice(0, 3)
      .map(([key, value]) => `${key}=${String(value)}`)
      .join(" · ");
  };

  const metricValue = (milliseconds: number | undefined, bypassed = false): string => {
    if (bypassed) return "BYPASS";
    return milliseconds === undefined
      ? "—"
      : `${Math.max(0, Math.round(milliseconds))}<small> ms</small>`;
  };

  const renderMetrics = (): void => {
    const lastSttIndex = lastIndexOf(events, "stt.final");
    const turnEvents = lastSttIndex >= 0 ? events.slice(lastSttIndex) : [];
    const beforeTurn = lastSttIndex >= 0 ? events.slice(0, lastSttIndex + 1) : events;
    const stt = events[lastSttIndex];
    const speechEnd = lastEvent(beforeTurn, "vad.speech_end");
    const asrBypassed = lastEvent(beforeTurn, "asr.bypassed");
    const admission = turnEvents.find((event) => event.event === "admission.final");
    const firstText = turnEvents.find((event) => event.event === "llm.delta");
    const firstAudio = turnEvents.find((event) => event.event === "tts.first_audio");
    const abortEvent = lastEvent(events, "abort.complete");
    const values = [
      metricValue(
        speechEnd && stt ? stt.elapsed_ms - speechEnd.elapsed_ms : undefined,
        Boolean(asrBypassed && stt && Math.abs(stt.elapsed_ms - asrBypassed.elapsed_ms) < 50),
      ),
      metricValue(stt && admission ? admission.elapsed_ms - stt.elapsed_ms : undefined),
      metricValue(
        admission && firstText ? firstText.elapsed_ms - admission.elapsed_ms : undefined,
      ),
      metricValue(firstText && firstAudio ? firstAudio.elapsed_ms - firstText.elapsed_ms : undefined),
      metricValue(
        (speechEnd ?? asrBypassed) && firstAudio
          ? firstAudio.elapsed_ms - (speechEnd ?? asrBypassed)!.elapsed_ms
          : undefined,
      ),
      metricValue(
        typeof abortEvent?.payload.duration_ms === "number"
          ? abortEvent.payload.duration_ms
          : undefined,
      ),
    ];
    const labels = [
      "Speech → ASR",
      "ASR → admission",
      "Admission → first text",
      "Text → first audio",
      "End → first audio",
      "Abort → silence",
    ];
    metrics.innerHTML = labels
      .map((label, index) => `<div><span>${label}</span><b>${values[index]}</b></div>`)
      .join("");
  };

  const renderEvents = (): void => {
    eventLog.innerHTML = events.length
      ? events
          .slice(-30)
          .map(
            (event, index, visible) =>
              `<div class="event-entry ${event.event.includes("bypassed") ? "bypassed" : index === visible.length - 1 ? "active" : ""}"><i></i><div><b>${escapeHtml(event.event)}</b><small>${escapeHtml(eventDetails(event))}</small></div><em>+${Math.round(event.elapsed_ms)} ms</em></div>`,
          )
          .join("")
      : '<div class="empty-event"><span>⌁</span><b>Đã mở phiên</b><small>Gửi text, audio hoặc nói vào microphone để tạo turn.</small></div>';
    eventLog.scrollTop = eventLog.scrollHeight;
    rawEvents.textContent = events.map((event) => JSON.stringify(event)).join("\n");
    renderMetrics();
  };

  const handleEvent = (event: LabEvent): void => {
    events.push(event);
    if (events.length > 240) events = events.slice(-240);
    if (event.event === "listen.start") {
      listening = true;
      currentAssistantMessage = undefined;
      setState("Đang lắng nghe", "running");
      prompt.textContent = "Assistant gate đang mở; VAD sẽ tự kết thúc câu nói.";
      orb.classList.add("running");
    } else if (event.event === "vad.speech_start") {
      setState("Đang nghe giọng nói", "running");
      prompt.textContent = "Silero VAD đã nhận speech; cứ nói tự nhiên.";
    } else if (event.event === "asr.start") {
      listening = false;
      setState("ASR đang giải mã", "running");
      prompt.textContent = "Zipformer Vietnamese đang tạo transcript.";
    } else if (event.event === "stt.final") {
      listening = false;
      if (event.payload.source !== "typed_text") {
        appendMessage("user", String(event.payload.text ?? ""));
      }
      setState("Đang đánh giá input", "running");
    } else if (event.event === "admission.final") {
      const disposition = String(event.payload.disposition ?? "unknown");
      const accepted = disposition === "accepted" || disposition === "end";
      listening = !accepted && disposition !== "interrupt";
      setState(
        accepted ? "AI đang xử lý" : "Input không tạo turn · đang nghe tiếp",
        "running",
      );
      if (listening) {
        prompt.textContent = "Input được bỏ qua an toàn; assistant vẫn đang lắng nghe.";
      }
    } else if (event.event === "llm.delta") {
      const text = String(event.payload.text ?? "");
      currentAssistantMessage ??= appendMessage("assistant", "");
      currentAssistantMessage.textContent += text;
      chat.scrollTop = chat.scrollHeight;
    } else if (event.event === "tts.start" || event.event === "tts.first_audio") {
      setState("AI đang nói", "running");
      prompt.textContent = "Bạn có thể bấm Ngắt AI ngay; generation cũ sẽ bị loại bỏ.";
    } else if (event.event === "tts.stop") {
      if (event.payload.cancelled) stopPlayback();
    } else if (event.event === "assistant.sleep") {
      listening = false;
      void stopMicrophone(false);
      setState("Assistant đang ngủ");
      prompt.textContent = "Hết timeout hoặc đã kết thúc hội thoại; có thể đánh thức lại.";
      orb.classList.remove("running");
      wakeButton.disabled = false;
    } else if (event.event === "abort.complete") {
      listening = true;
      setState("Đã ngắt · đang nghe tiếp", "running");
      interruptButton.disabled = false;
    } else if (event.event === "input.busy") {
      callbacks.toast(`Assistant đang ${String(event.payload.state ?? "bận")}.`);
    } else if (event.event === "turn.error") {
      setState("Turn gặp lỗi", "error");
      callbacks.toast(`Voice turn lỗi: ${String(event.payload.code ?? event.payload.error_type)}`);
    }
    updateInputPanels();
    renderEvents();
  };

  const resetSessionUi = (): void => {
    connected = false;
    listening = false;
    sessionId = "";
    issued = undefined;
    events = [];
    currentAssistantMessage = undefined;
    sessionIdLabel.textContent = "NO SESSION";
    lockSetup(false);
    interruptButton.disabled = true;
    wakeButton.disabled = true;
    setState("Sẵn sàng");
    prompt.textContent = "Chọn trợ lý và đầu vào, sau đó bắt đầu phiên.";
    orb.classList.remove("running");
    startButton.textContent = "Bắt đầu phiên thử";
    chat.innerHTML =
      '<div class="lab-message system">Phiên Lab không lưu transcript hoặc audio vào Manager.</div>';
    eventLog.innerHTML =
      '<div class="empty-event"><span>⌁</span><b>Chưa có phiên đang chạy</b><small>Token một lần chỉ được cấp khi bạn bấm bắt đầu.</small></div>';
    rawEvents.textContent = "";
    renderMetrics();
    updateInputPanels();
  };

  const stopMicrophone = async (notifyServer: boolean): Promise<void> => {
    if (!micStream) return;
    micNode?.disconnect();
    micSource?.disconnect();
    micSilentGain?.disconnect();
    for (const track of micStream.getTracks()) track.stop();
    micStream = undefined;
    micNode = undefined;
    micSource = undefined;
    micSilentGain = undefined;
    micPcm = new Int16Array(0);
    micButton.textContent = "🎙 Bật microphone";
    if (notifyServer) send({ type: "lab.audio.end" });
  };

  const closeSession = async (notifyServer: boolean): Promise<void> => {
    audioSendGeneration += 1;
    await stopMicrophone(false);
    stopPlayback();
    if (notifyServer) send({ type: "lab.close" });
    const current = socket;
    socket = undefined;
    if (current && current.readyState < WebSocket.CLOSING) current.close(1000, "lab closed");
    resetSessionUi();
  };

  const handleHello = (hello: LabHello): void => {
    sessionId = hello.session_id;
    outputSampleRate = hello.audio.output_sample_rate;
    connected = true;
    listening = true;
    sessionIdLabel.textContent = sessionId.slice(0, 12).toUpperCase();
    startButton.textContent = "Phiên đang chạy";
    agentLabel.textContent = issued
      ? `${issued.agent.name} · v${issued.agent.version}`
      : "Published agent";
    engineLabel.textContent = `${hello.input_mode} · ${hello.mcp_mode}`;
    lockSetup(true);
    interruptButton.disabled = false;
    setState("Đang lắng nghe", "running");
    prompt.textContent = "Assistant gate đã mở như khi bấm nút trên robot.";
    updateInputPanels();
  };

  const startSession = async (): Promise<void> => {
    if (connected || socket) return;
    const agentId = agentSelect.value;
    if (!agentId) return callbacks.toast("Hãy publish và chọn một trợ lý trước.");
    const mcpMode = selectedMcpMode();
    const deviceId = mcpMode === "selected_device" ? deviceSelect.value : undefined;
    if (mcpMode === "selected_device" && !deviceId) {
      return callbacks.toast("Hãy chọn thiết bị đang có voice session hoặc dùng MCP mô phỏng.");
    }
    startButton.disabled = true;
    startButton.textContent = "Đang cấp token…";
    setState("Đang mở phiên", "running");
    try {
      await ensureAudioContext();
      issued = await callbacks.createSession({
        agentId,
        inputMode: selectedInputMode(),
        mcpMode,
        ...(deviceId ? { deviceId } : {}),
      });
      let token = issued.token;
      socket = new WebSocket(issued.websocketUrl);
      socket.binaryType = "arraybuffer";
      socket.addEventListener("open", () => {
        socket?.send(JSON.stringify({ type: "lab.auth", token }));
        token = "";
      });
      socket.addEventListener("message", (message) => {
        if (message.data instanceof ArrayBuffer) {
          void playPcm(message.data);
          return;
        }
        if (typeof message.data !== "string") return;
        const payload = JSON.parse(message.data) as LabHello | LabEvent;
        if (payload.type === "lab.hello") handleHello(payload);
        else if (payload.type === "lab.event") handleEvent(payload);
      });
      socket.addEventListener("error", () => {
        setState("Không kết nối được Voice Lab", "error");
      });
      socket.addEventListener("close", (closeEvent) => {
        const wasConnected = connected;
        socket = undefined;
        if (sessionId) void closeSession(false);
        else resetSessionUi();
        if (closeEvent.code !== 1000) {
          callbacks.toast(
            closeEvent.reason ||
              (wasConnected ? "Voice Lab đã ngắt kết nối." : "Token Lab bị từ chối hoặc hết hạn."),
          );
        }
      });
    } catch (error) {
      socket = undefined;
      resetSessionUi();
      callbacks.toast(error instanceof Error ? error.message : "Không thể mở Realtime Lab.");
    }
  };

  const resampleFile = async (file: File): Promise<Int16Array> => {
    const context = await ensureAudioContext();
    const decoded = await context.decodeAudioData(await file.arrayBuffer());
    if (decoded.duration > 20) throw new Error("Audio Replay giới hạn 20 giây mỗi file.");
    const mono = new Float32Array(decoded.length);
    for (let channelIndex = 0; channelIndex < decoded.numberOfChannels; channelIndex += 1) {
      const source = decoded.getChannelData(channelIndex);
      for (let index = 0; index < source.length; index += 1) {
        mono[index] = (mono[index] ?? 0) + source[index]! / decoded.numberOfChannels;
      }
    }
    const resampler = new StreamingResampler(decoded.sampleRate, 16_000);
    const resampled = resampler.push(mono);
    const pcm = new Int16Array(resampled.length);
    for (let index = 0; index < resampled.length; index += 1) {
      const value = Math.max(-1, Math.min(1, resampled[index]!));
      pcm[index] = value < 0 ? value * 32768 : value * 32767;
    }
    return pcm;
  };

  const replayAudio = async (): Promise<void> => {
    const file = audioFile.files?.[0];
    if (!file || !connected || !listening) return;
    replayButton.disabled = true;
    const generation = ++audioSendGeneration;
    try {
      const pcm = await resampleFile(file);
      if (!send({ type: "lab.audio.start", encoding: "pcm_s16le", sample_rate: 16_000 })) {
        throw new Error("Voice Lab chưa kết nối.");
      }
      const frameSamples = 960;
      const startedAt = performance.now();
      for (let offset = 0, frame = 0; offset < pcm.length; offset += frameSamples, frame += 1) {
        if (generation !== audioSendGeneration || !socket || socket.readyState !== WebSocket.OPEN) {
          return;
        }
        while (socket.bufferedAmount > 256 * 1024) await delay(10);
        const chunk = pcm.slice(offset, offset + frameSamples);
        socket.send(chunk.buffer);
        const target = startedAt + (frame + 1) * 60;
        await delay(Math.max(0, target - performance.now()));
      }
      send({ type: "lab.audio.end" });
    } catch (error) {
      callbacks.toast(error instanceof Error ? error.message : "Không thể replay audio.");
      send({ type: "lab.audio.end" });
    } finally {
      updateInputPanels();
    }
  };

  const appendMicPcm = (samples: Float32Array): void => {
    const next = new Int16Array(micPcm.length + samples.length);
    next.set(micPcm);
    for (let index = 0; index < samples.length; index += 1) {
      const value = Math.max(-1, Math.min(1, samples[index]!));
      next[micPcm.length + index] = value < 0 ? value * 32768 : value * 32767;
    }
    micPcm = next;
    while (micPcm.length >= 960 && socket?.readyState === WebSocket.OPEN) {
      const frame = micPcm.slice(0, 960);
      micPcm = micPcm.slice(960);
      socket.send(frame.buffer);
    }
  };

  const startMicrophone = async (): Promise<void> => {
    if (!connected || micStream) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("Live Mic cần HTTPS hoặc localhost. Hãy dùng Audio Replay trên LAN HTTP.");
    }
    const context = await ensureAudioContext();
    if (!context.audioWorklet) throw new Error("Browser không hỗ trợ AudioWorklet.");
    const workletSource = `class VeeteeCapture extends AudioWorkletProcessor { process(inputs) { const channel = inputs[0] && inputs[0][0]; if (channel) this.port.postMessage(channel.slice(0)); return true; } } registerProcessor('veetee-capture', VeeteeCapture);`;
    const moduleUrl = URL.createObjectURL(new Blob([workletSource], { type: "text/javascript" }));
    try {
      await context.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    micSource = context.createMediaStreamSource(micStream);
    micNode = new AudioWorkletNode(context, "veetee-capture");
    micSilentGain = context.createGain();
    micSilentGain.gain.value = 0;
    const resampler = new StreamingResampler(context.sampleRate, 16_000);
    micNode.port.onmessage = (message: MessageEvent<Float32Array>) => {
      if (message.data instanceof Float32Array) appendMicPcm(resampler.push(message.data));
    };
    micSource.connect(micNode).connect(micSilentGain).connect(context.destination);
    send({ type: "lab.audio.start", encoding: "pcm_s16le", sample_rate: 16_000 });
    micButton.textContent = "■ Tắt microphone";
    micHint.textContent = "Mic đang mở · browser AEC/NS bật; đây chưa phải AEC của board ESP32.";
  };

  inputModeSelect.addEventListener("change", updateInputPanels, { signal: abort.signal });
  mcpModeSelect.addEventListener("change", updateInputPanels, { signal: abort.signal });
  audioFile.addEventListener(
    "change",
    () => {
      const file = audioFile.files?.[0];
      audioMeta.textContent = file
        ? `${file.name} · ${(file.size / 1024).toFixed(1)} KiB · sẽ gửi realtime PCM16 16 kHz`
        : "WAV/MP3/OGG được browser decode, resample mono 16 kHz và gửi realtime.";
      updateInputPanels();
    },
    { signal: abort.signal },
  );
  startButton.addEventListener("click", () => void startSession(), { signal: abort.signal });
  endButton.addEventListener("click", () => void closeSession(true), { signal: abort.signal });
  interruptButton.addEventListener(
    "click",
    () => {
      audioSendGeneration += 1;
      stopPlayback();
      interruptButton.disabled = true;
      send({ type: "lab.abort", reason: "web_interrupt" });
      setState("Đang hủy generation hiện tại", "running");
    },
    { signal: abort.signal },
  );
  wakeButton.addEventListener(
    "click",
    () => {
      wakeButton.disabled = true;
      send({ type: "lab.wake" });
    },
    { signal: abort.signal },
  );
  rawToggle.addEventListener(
    "click",
    () => {
      rawEvents.hidden = !rawEvents.hidden;
      eventLog.hidden = !rawEvents.hidden;
    },
    { signal: abort.signal },
  );
  textForm.addEventListener(
    "submit",
    (event) => {
      event.preventDefault();
      const text = textInput.value.trim();
      if (!text || !listening) return;
      appendMessage("user", text);
      currentAssistantMessage = undefined;
      if (send({ type: "lab.text", text })) {
        textInput.value = "";
        listening = false;
        updateInputPanels();
      }
    },
    { signal: abort.signal },
  );
  replayButton.addEventListener("click", () => void replayAudio(), { signal: abort.signal });
  micButton.addEventListener(
    "click",
    () => {
      if (micStream) void stopMicrophone(true);
      else void startMicrophone().catch((error: unknown) => {
        callbacks.toast(error instanceof Error ? error.message : "Không thể mở microphone.");
      });
    },
    { signal: abort.signal },
  );

  resetSessionUi();

  return {
    updateCatalog(nextAgents: Agent[], nextDevices: Device[]): void {
      agents = nextAgents;
      devices = nextDevices;
      const selectedAgent = agentSelect.value;
      const selectedDevice = deviceSelect.value;
      agentSelect.innerHTML = agents.length
        ? agents
            .filter((agent) => agent.publishedVersion > 0)
            .map(
              (agent) =>
                `<option value="${escapeHtml(agent.id)}">${escapeHtml(agent.name)} · v${agent.publishedVersion} · ${escapeHtml(agent.defaultLocale)}</option>`,
            )
            .join("")
        : '<option value="">Chưa có agent đã publish</option>';
      deviceSelect.innerHTML = devices.length
        ? devices
            .map(
              (device) =>
                `<option value="${escapeHtml(device.id)}">${escapeHtml(device.name)} · ${escapeHtml(device.status)}</option>`,
            )
            .join("")
        : '<option value="">Chưa có thiết bị</option>';
      if (agents.some((agent) => agent.id === selectedAgent)) agentSelect.value = selectedAgent;
      if (devices.some((device) => device.id === selectedDevice)) {
        deviceSelect.value = selectedDevice;
      }
      const agent = agents.find((item) => item.id === agentSelect.value) ?? agents[0];
      const device = devices.find((item) => item.id === deviceSelect.value) ?? devices[0];
      agentLabel.textContent = agent
        ? `${agent.name} · v${agent.publishedVersion}`
        : "Chưa có agent";
      deviceLabel.textContent = device ? `Web Simulator · ${device.name}` : "Web Simulator";
      engineLabel.textContent = `${selectedInputMode()} · ${selectedMcpMode()}`;
      updateInputPanels();
    },
    destroy(): void {
      abort.abort();
      void closeSession(true);
      void audioContext?.close();
    },
  };
}
