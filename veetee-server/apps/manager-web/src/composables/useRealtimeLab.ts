import { computed, onBeforeUnmount, ref } from "vue";

import type { LabSession as IssuedLabSession } from "../api/schemas";

export type LabInputMode = IssuedLabSession["inputMode"];
export type LabMcpMode = IssuedLabSession["mcpMode"];

export interface LabEvent {
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
  input_mode: LabInputMode;
  mcp_mode: LabMcpMode;
  audio: { output_sample_rate: number };
  fidelity: Record<string, string>;
  prompt?: {
    applied: boolean;
    version: number;
    language: string;
    personality: string;
  };
}

export interface LabMessage {
  id: number;
  kind: "user" | "assistant" | "system";
  text: string;
}

interface LabCallbacks {
  createSession(input: {
    agentId: string;
    inputMode: LabInputMode;
    mcpMode: LabMcpMode;
    deviceId?: string;
  }): Promise<IssuedLabSession>;
  toast(message: string, tone?: "success" | "danger" | "info"): void;
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

  constructor(private readonly sourceRate: number, private readonly targetRate: number) {}

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

export function labEventDetails(event: LabEvent): string {
  const payload = event.payload;
  if (event.event === "stt.final") return `${String(payload.source ?? "audio")} · ${String(payload.text ?? "")}`;
  if (event.event === "admission.final") return `${String(payload.disposition ?? "unknown")} · ${String(payload.reason_code ?? "")}`;
  if (event.event === "planner.final") return `${String(payload.action ?? "unknown")} · ${String(payload.intent ?? "")}`;
  if (event.event.startsWith("mcp.")) return `${String(payload.tool ?? "tool")} · ${String(payload.duration_ms ?? "…")} ms`;
  if (event.event === "abort.complete") return `${String(payload.reason ?? "interrupt")} · ${String(payload.duration_ms ?? "…")} ms`;
  if (event.event.includes("bypassed")) return String(payload.reason ?? "bypassed");
  if (event.event === "turn.error") return String(payload.code ?? payload.error_type ?? "error");
  return Object.entries(payload).slice(0, 3).map(([key, value]) => `${key}=${String(value)}`).join(" · ");
}

export function useRealtimeLab(callbacks: LabCallbacks) {
  const agentId = ref("");
  const inputMode = ref<LabInputMode>("text");
  const mcpMode = ref<LabMcpMode>("simulated");
  const deviceId = ref("");
  const connected = ref(false);
  const listening = ref(false);
  const starting = ref(false);
  const state = ref("Sẵn sàng");
  const stateTone = ref<"idle" | "running" | "error">("idle");
  const prompt = ref("Chọn trợ lý và đầu vào, sau đó bắt đầu phiên.");
  const activePrompt = ref<LabHello["prompt"]>();
  const sessionId = ref("");
  const issued = ref<IssuedLabSession>();
  const events = ref<LabEvent[]>([]);
  const messages = ref<LabMessage[]>([
    { id: 1, kind: "system", text: "Phiên Lab không lưu transcript hoặc audio vào Manager." },
  ]);
  const replayFile = ref<File>();
  const replayBusy = ref(false);
  const micActive = ref(false);
  const showRaw = ref(false);
  let nextMessageId = 2;
  let currentAssistantId: number | undefined;
  let socket: WebSocket | undefined;
  let outputSampleRate = 24_000;
  let audioContext: AudioContext | undefined;
  let nextPlaybackAt = 0;
  const playbackSources = new Set<AudioBufferSourceNode>();
  let audioSendGeneration = 0;
  let micStream: MediaStream | undefined;
  let micSource: MediaStreamAudioSourceNode | undefined;
  let micNode: AudioWorkletNode | undefined;
  let micSilentGain: GainNode | undefined;
  let micPcm = new Int16Array(0);
  let closing = false;

  const locked = computed(() => connected.value || starting.value);
  const canSubmit = computed(() => connected.value && listening.value);
  const rawEvents = computed(() => events.value.map((event) => JSON.stringify(event)).join("\n"));
  const replayMeta = computed(() => replayFile.value
    ? `${replayFile.value.name} · ${(replayFile.value.size / 1024).toFixed(1)} KiB · gửi realtime PCM16 16 kHz`
    : "WAV/MP3/OGG được browser decode, resample mono 16 kHz và gửi realtime.");

  const metrics = computed(() => {
    const current = events.value;
    const lastSttIndex = lastIndexOf(current, "stt.final");
    const turnEvents = lastSttIndex >= 0 ? current.slice(lastSttIndex) : [];
    const beforeTurn = lastSttIndex >= 0 ? current.slice(0, lastSttIndex + 1) : current;
    const stt = current[lastSttIndex];
    const speechEnd = lastEvent(beforeTurn, "vad.speech_end");
    const asrBypassed = lastEvent(beforeTurn, "asr.bypassed");
    const admission = turnEvents.find((event) => event.event === "admission.final");
    const firstText = turnEvents.find((event) => event.event === "llm.delta");
    const firstAudio = turnEvents.find((event) => event.event === "tts.first_audio");
    const abort = lastEvent(current, "abort.complete");
    const value = (milliseconds?: number, bypassed = false): string => bypassed ? "BYPASS" : milliseconds === undefined ? "—" : `${Math.max(0, Math.round(milliseconds))} ms`;
    return [
      { label: "Speech → ASR", value: value(speechEnd && stt ? stt.elapsed_ms - speechEnd.elapsed_ms : undefined, Boolean(asrBypassed && stt && Math.abs(stt.elapsed_ms - asrBypassed.elapsed_ms) < 50)) },
      { label: "ASR → admission", value: value(stt && admission ? admission.elapsed_ms - stt.elapsed_ms : undefined) },
      { label: "Admission → text", value: value(admission && firstText ? firstText.elapsed_ms - admission.elapsed_ms : undefined) },
      { label: "Text → first audio", value: value(firstText && firstAudio ? firstAudio.elapsed_ms - firstText.elapsed_ms : undefined) },
      { label: "End → first audio", value: value((speechEnd ?? asrBypassed) && firstAudio ? firstAudio.elapsed_ms - (speechEnd ?? asrBypassed)!.elapsed_ms : undefined) },
      { label: "Abort → silence", value: value(typeof abort?.payload.duration_ms === "number" ? abort.payload.duration_ms : undefined) },
    ];
  });

  function setState(label: string, tone: "idle" | "running" | "error" = "idle", helper?: string): void {
    state.value = label;
    stateTone.value = tone;
    if (helper) prompt.value = helper;
  }

  function appendMessage(kind: LabMessage["kind"], text: string): number {
    const id = nextMessageId++;
    messages.value.push({ id, kind, text });
    return id;
  }

  function appendAssistant(text: string): void {
    if (!currentAssistantId) currentAssistantId = appendMessage("assistant", "");
    const message = messages.value.find((item) => item.id === currentAssistantId);
    if (message) message.text += text;
  }

  function send(payload: Record<string, unknown>): boolean {
    if (!socket || socket.readyState !== WebSocket.OPEN || !sessionId.value) return false;
    socket.send(JSON.stringify({ ...payload, session_id: sessionId.value }));
    return true;
  }

  function stopPlayback(): void {
    for (const source of playbackSources) {
      try { source.stop(); } catch { /* Source may have ended already. */ }
    }
    playbackSources.clear();
    nextPlaybackAt = audioContext?.currentTime ?? 0;
  }

  async function ensureAudioContext(): Promise<AudioContext> {
    audioContext ??= new AudioContext({ latencyHint: "interactive" });
    if (audioContext.state === "suspended") await audioContext.resume();
    return audioContext;
  }

  async function playPcm(data: ArrayBuffer): Promise<void> {
    const context = await ensureAudioContext();
    const samples = new Int16Array(data);
    if (!samples.length) return;
    const buffer = context.createBuffer(1, samples.length, outputSampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index]! / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.015, nextPlaybackAt);
    nextPlaybackAt = startAt + buffer.duration;
    playbackSources.add(source);
    source.onended = () => playbackSources.delete(source);
    source.start(startAt);
  }

  async function stopMicrophone(notifyServer: boolean): Promise<void> {
    if (!micStream) return;
    micNode?.disconnect(); micSource?.disconnect(); micSilentGain?.disconnect();
    for (const track of micStream.getTracks()) track.stop();
    micStream = undefined; micNode = undefined; micSource = undefined; micSilentGain = undefined;
    micPcm = new Int16Array(0);
    micActive.value = false;
    if (notifyServer) send({ type: "lab.audio.end" });
  }

  function reset(): void {
    connected.value = false;
    listening.value = false;
    starting.value = false;
    sessionId.value = "";
    issued.value = undefined;
    activePrompt.value = undefined;
    events.value = [];
    messages.value = [{ id: nextMessageId++, kind: "system", text: "Phiên Lab không lưu transcript hoặc audio vào Manager." }];
    currentAssistantId = undefined;
    replayFile.value = undefined;
    showRaw.value = false;
    setState("Sẵn sàng", "idle", "Chọn trợ lý và đầu vào, sau đó bắt đầu phiên.");
  }

  async function closeSession(notifyServer = true): Promise<void> {
    if (closing) return;
    closing = true;
    audioSendGeneration += 1;
    await stopMicrophone(false);
    stopPlayback();
    if (notifyServer) send({ type: "lab.close" });
    const current = socket;
    socket = undefined;
    if (current && current.readyState < WebSocket.CLOSING) current.close(1000, "lab closed");
    reset();
    closing = false;
  }

  function handleEvent(event: LabEvent): void {
    events.value = [...events.value.slice(-239), event];
    if (event.event === "listen.start") {
      listening.value = true; currentAssistantId = undefined;
      setState("Đang lắng nghe", "running", "Assistant gate đang mở; VAD sẽ tự kết thúc câu nói.");
    } else if (event.event === "vad.speech_start") {
      setState("Đang nghe giọng nói", "running", "Silero VAD đã nhận speech; cứ nói tự nhiên.");
    } else if (event.event === "asr.start") {
      listening.value = false;
      setState("ASR đang giải mã", "running", "Zipformer Vietnamese đang tạo transcript.");
    } else if (event.event === "stt.final") {
      listening.value = false;
      if (event.payload.source !== "typed_text") appendMessage("user", String(event.payload.text ?? ""));
      setState("Đang đánh giá input", "running");
    } else if (event.event === "admission.final") {
      const disposition = String(event.payload.disposition ?? "unknown");
      const accepted = disposition === "accepted" || disposition === "end";
      listening.value = !accepted && disposition !== "interrupt";
      setState(accepted ? "AI đang xử lý" : "Input không tạo turn · đang nghe tiếp", "running", listening.value ? "Input được bỏ qua an toàn; assistant vẫn đang lắng nghe." : undefined);
    } else if (event.event === "llm.delta") {
      appendAssistant(String(event.payload.text ?? ""));
    } else if (event.event === "tts.start" || event.event === "tts.first_audio") {
      setState("AI đang nói", "running", "Bạn có thể bấm Ngắt AI; generation cũ sẽ bị loại bỏ.");
    } else if (event.event === "tts.stop" && event.payload.cancelled) {
      stopPlayback();
    } else if (event.event === "assistant.sleep") {
      listening.value = false;
      void stopMicrophone(false);
      setState("Assistant đang ngủ", "idle", "Hết timeout hoặc đã kết thúc hội thoại; có thể đánh thức lại.");
    } else if (event.event === "abort.complete") {
      listening.value = true;
      setState("Đã ngắt · đang nghe tiếp", "running");
    } else if (event.event === "input.busy") {
      callbacks.toast(`Assistant đang ${String(event.payload.state ?? "bận")}.`, "info");
    } else if (event.event === "turn.error") {
      setState("Turn gặp lỗi", "error");
      callbacks.toast(`Voice turn lỗi: ${String(event.payload.code ?? event.payload.error_type)}`, "danger");
    }
  }

  function handleHello(hello: LabHello): void {
    sessionId.value = hello.session_id;
    activePrompt.value = hello.prompt;
    outputSampleRate = hello.audio.output_sample_rate;
    connected.value = true;
    listening.value = true;
    starting.value = false;
    setState("Đang lắng nghe", "running", "Assistant gate đã mở như khi bấm nút trên robot.");
  }

  async function startSession(): Promise<void> {
    if (locked.value) return;
    if (!agentId.value) return callbacks.toast("Hãy publish và chọn một trợ lý trước.", "danger");
    if (mcpMode.value === "selected_device" && !deviceId.value) return callbacks.toast("Hãy chọn thiết bị hoặc dùng MCP mô phỏng.", "danger");
    starting.value = true;
    setState("Đang mở phiên", "running", "Manager đang cấp token WebSocket dùng một lần.");
    try {
      await ensureAudioContext();
      issued.value = await callbacks.createSession({
        agentId: agentId.value, inputMode: inputMode.value, mcpMode: mcpMode.value,
        ...(mcpMode.value === "selected_device" ? { deviceId: deviceId.value } : {}),
      });
      let token = issued.value.token;
      const current = new WebSocket(issued.value.websocketUrl);
      socket = current;
      current.binaryType = "arraybuffer";
      current.addEventListener("open", () => { current.send(JSON.stringify({ type: "lab.auth", token })); token = ""; });
      current.addEventListener("message", (message) => {
        if (message.data instanceof ArrayBuffer) { void playPcm(message.data); return; }
        if (typeof message.data !== "string") return;
        const payload = JSON.parse(message.data) as LabHello | LabEvent;
        if (payload.type === "lab.hello") handleHello(payload); else if (payload.type === "lab.event") handleEvent(payload);
      });
      current.addEventListener("error", () => setState("Không kết nối được Voice Lab", "error"));
      current.addEventListener("close", (event) => {
        if (socket !== current) return;
        const wasConnected = connected.value;
        socket = undefined;
        reset();
        if (event.code !== 1000) callbacks.toast(event.reason || (wasConnected ? "Voice Lab đã ngắt kết nối." : "Token Lab bị từ chối hoặc hết hạn."), "danger");
      });
    } catch (exception) {
      socket = undefined; reset();
      callbacks.toast(exception instanceof Error ? exception.message : "Không thể mở Realtime Lab.", "danger");
    }
  }

  function submitText(text: string): boolean {
    const value = text.trim();
    if (!value || !canSubmit.value) return false;
    appendMessage("user", value);
    currentAssistantId = undefined;
    if (!send({ type: "lab.text", text: value })) return false;
    listening.value = false;
    return true;
  }

  function interrupt(): void {
    if (!connected.value) return;
    audioSendGeneration += 1;
    stopPlayback();
    send({ type: "lab.abort", reason: "web_interrupt" });
    setState("Đang hủy generation hiện tại", "running");
  }

  function wake(): void {
    if (connected.value) send({ type: "lab.wake" });
  }

  async function resampleFile(file: File): Promise<Int16Array> {
    const context = await ensureAudioContext();
    const decoded = await context.decodeAudioData(await file.arrayBuffer());
    if (decoded.duration > 20) throw new Error("Audio Replay giới hạn 20 giây mỗi file.");
    const mono = new Float32Array(decoded.length);
    for (let channelIndex = 0; channelIndex < decoded.numberOfChannels; channelIndex += 1) {
      const source = decoded.getChannelData(channelIndex);
      for (let index = 0; index < source.length; index += 1) mono[index] = (mono[index] ?? 0) + source[index]! / decoded.numberOfChannels;
    }
    const resampled = new StreamingResampler(decoded.sampleRate, 16_000).push(mono);
    return Int16Array.from(resampled, (sample) => {
      const value = Math.max(-1, Math.min(1, sample));
      return value < 0 ? value * 32768 : value * 32767;
    });
  }

  async function replayAudio(): Promise<void> {
    if (!replayFile.value || !canSubmit.value) return;
    replayBusy.value = true;
    const generation = ++audioSendGeneration;
    try {
      const pcm = await resampleFile(replayFile.value);
      if (!send({ type: "lab.audio.start", encoding: "pcm_s16le", sample_rate: 16_000 })) throw new Error("Voice Lab chưa kết nối.");
      const frameSamples = 960;
      const startedAt = performance.now();
      for (let offset = 0, frame = 0; offset < pcm.length; offset += frameSamples, frame += 1) {
        if (generation !== audioSendGeneration || !socket || socket.readyState !== WebSocket.OPEN) return;
        while (socket.bufferedAmount > 256 * 1024) await delay(10);
        socket.send(pcm.slice(offset, offset + frameSamples).buffer);
        await delay(Math.max(0, startedAt + (frame + 1) * 60 - performance.now()));
      }
      send({ type: "lab.audio.end" });
    } catch (exception) {
      callbacks.toast(exception instanceof Error ? exception.message : "Không thể replay audio.", "danger");
      send({ type: "lab.audio.end" });
    } finally { replayBusy.value = false; }
  }

  function appendMicPcm(samples: Float32Array): void {
    const next = new Int16Array(micPcm.length + samples.length);
    next.set(micPcm);
    for (let index = 0; index < samples.length; index += 1) {
      const value = Math.max(-1, Math.min(1, samples[index]!));
      next[micPcm.length + index] = value < 0 ? value * 32768 : value * 32767;
    }
    micPcm = next;
    while (micPcm.length >= 960 && socket?.readyState === WebSocket.OPEN) {
      socket.send(micPcm.slice(0, 960).buffer);
      micPcm = micPcm.slice(960);
    }
  }

  async function startMicrophone(): Promise<void> {
    if (!connected.value || micStream) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Live Mic cần HTTPS hoặc localhost. Hãy dùng Audio Replay trên LAN HTTP.");
    const context = await ensureAudioContext();
    if (!context.audioWorklet) throw new Error("Browser không hỗ trợ AudioWorklet.");
    const workletSource = "class VeeteeCapture extends AudioWorkletProcessor { process(inputs) { const channel = inputs[0] && inputs[0][0]; if (channel) this.port.postMessage(channel.slice(0)); return true; } } registerProcessor('veetee-capture', VeeteeCapture);";
    const moduleUrl = URL.createObjectURL(new Blob([workletSource], { type: "text/javascript" }));
    try { await context.audioWorklet.addModule(moduleUrl); } finally { URL.revokeObjectURL(moduleUrl); }
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    micSource = context.createMediaStreamSource(micStream);
    micNode = new AudioWorkletNode(context, "veetee-capture");
    micSilentGain = context.createGain(); micSilentGain.gain.value = 0;
    const resampler = new StreamingResampler(context.sampleRate, 16_000);
    micNode.port.onmessage = (message: MessageEvent<Float32Array>) => { if (message.data instanceof Float32Array) appendMicPcm(resampler.push(message.data)); };
    micSource.connect(micNode).connect(micSilentGain).connect(context.destination);
    send({ type: "lab.audio.start", encoding: "pcm_s16le", sample_rate: 16_000 });
    micActive.value = true;
  }

  async function toggleMicrophone(): Promise<void> {
    try { if (micStream) await stopMicrophone(true); else await startMicrophone(); }
    catch (exception) { callbacks.toast(exception instanceof Error ? exception.message : "Không thể mở microphone.", "danger"); }
  }

  onBeforeUnmount(() => { void closeSession(true); void audioContext?.close(); });

  return {
    agentId, inputMode, mcpMode, deviceId, connected, listening, starting, locked, canSubmit,
    state, stateTone, prompt, activePrompt, sessionId, issued, events, messages, metrics, rawEvents, showRaw,
    replayFile, replayMeta, replayBusy, micActive,
    startSession, closeSession, submitText, interrupt, wake, replayAudio, toggleMicrophone,
  };
}
