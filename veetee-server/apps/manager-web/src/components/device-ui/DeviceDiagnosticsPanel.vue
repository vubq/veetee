<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type {
  AudioDiagnosticSession,
  DeviceHealth,
  DeviceSelfTest,
} from "../../api/schemas";
import { VtBadge, VtButton, VtIcon, VtSelect } from "../ui";

type TaskRuntimeHealth = NonNullable<DeviceHealth["tasks"]>["capture"];

const props = defineProps<{
  deviceId: string;
  getHealth: (deviceId: string) => Promise<DeviceHealth>;
  startAudio: (deviceId: string, durationSeconds: number) => Promise<AudioDiagnosticSession>;
  runSelfTest: (deviceId: string) => Promise<DeviceSelfTest>;
}>();

const health = ref<DeviceHealth>();
const audioSession = ref<AudioDiagnosticSession>();
const selfTest = ref<DeviceSelfTest>();
const duration = ref("5");
const loading = ref(false);
const refreshing = ref(false);
const selfTestBusy = ref(false);
const audioBusy = ref(false);
const error = ref("");
const selfTestError = ref("");
let pollTimer: number | undefined;

const diagnostic = computed(() => health.value?.audio.diagnostic ?? audioSession.value);
const isRunning = computed(() => diagnostic.value?.state === "running");
const remainingSeconds = computed(() => {
  if (!isRunning.value || !health.value || !diagnostic.value) return 0;
  return Math.max(0, Math.ceil((diagnostic.value.endsMs - health.value.device.uptimeMs) / 1000));
});
const taskHeadroomRows = computed(() => {
  const tasks = health.value?.tasks;
  if (!tasks) return [];
  return [
    { id: "capture", label: "Capture audio", task: tasks.capture },
    { id: "playback", label: "Playback audio", task: tasks.playback },
    { id: "wake", label: "Wake detector", task: tasks.wake },
    { id: "websocket_control", label: "WebSocket control", task: tasks.websocketControl },
  ];
});
const stackHeadroomHealthy = computed(() => {
  const tasks = health.value?.tasks;
  if (!tasks) return true;
  return taskHeadroomRows.value.every(({ task }) =>
    !task.expected ||
    (task.running && task.stackFreeBytes >= tasks.minimumStackFreeBytes),
  );
});
const softwareHealthy = computed(() => {
  const value = health.value;
  return Boolean(
    value?.network.connected &&
      value.audio.captureTaskRunning &&
      value.audio.playbackTaskRunning &&
      value.resources.wakeResourceHealthy &&
      value.resources.uiPackHealthy &&
      stackHeadroomHealthy.value,
  );
});

async function loadHealth(initial = false): Promise<void> {
  if (initial) loading.value = true;
  else refreshing.value = true;
  error.value = "";
  try {
    health.value = await props.getHealth(props.deviceId);
    if (health.value.audio.diagnostic.state !== "not_run") {
      audioSession.value = health.value.audio.diagnostic;
    }
    if (health.value.audio.diagnostic.state === "running") startPolling();
    else stopPolling();
  } catch (exception) {
    error.value = diagnosticErrorMessage(exception, "Không thể đọc health thiết bị.");
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}

function startPolling(): void {
  if (pollTimer !== undefined) return;
  pollTimer = window.setInterval(() => {
    void loadHealth();
  }, 1_000);
}

function stopPolling(): void {
  if (pollTimer === undefined) return;
  window.clearInterval(pollTimer);
  pollTimer = undefined;
}

function diagnosticErrorMessage(exception: unknown, fallback: string): string {
  if (
    exception &&
    typeof exception === "object" &&
    "status" in exception &&
    (exception as { status?: unknown }).status === 409
  ) {
    return "Thiết bị chưa mở phiên voice/MCP. Bấm nút đánh thức thiết bị rồi thử lại.";
  }
  return exception instanceof Error ? exception.message : fallback;
}

async function startAudioDiagnostic(): Promise<void> {
  if (!window.confirm("Bắt đầu phiên đo audio metrics-only? Không có raw audio được lưu hoặc truyền.")) {
    return;
  }
  audioBusy.value = true;
  error.value = "";
  try {
    audioSession.value = await props.startAudio(props.deviceId, Number(duration.value));
    startPolling();
    await loadHealth();
  } catch (exception) {
    error.value = diagnosticErrorMessage(exception, "Không thể bắt đầu audio debugger.");
  } finally {
    audioBusy.value = false;
  }
}

async function runSelfTest(): Promise<void> {
  if (!window.confirm("Chạy self-test không phá trạng thái? Self-test không đổi Wi-Fi/NVS và không phát tone.")) {
    return;
  }
  selfTestBusy.value = true;
  selfTestError.value = "";
  try {
    selfTest.value = await props.runSelfTest(props.deviceId);
  } catch (exception) {
    selfTestError.value = diagnosticErrorMessage(exception, "Self-test thất bại.");
  } finally {
    selfTestBusy.value = false;
  }
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(value);
}

function taskHealthy(task: TaskRuntimeHealth): boolean {
  const minimum = health.value?.tasks?.minimumStackFreeBytes ?? 0;
  return !task.expected ||
    (task.running && task.stackFreeBytes >= minimum);
}

function taskStatus(task: TaskRuntimeHealth): string {
  if (!task.expected) return "Không kích hoạt";
  if (!task.running) return "Đã dừng";
  return taskHealthy(task) ? "An toàn" : "Sắp cạn stack";
}

function taskStatusTone(task: TaskRuntimeHealth): "success" | "danger" | "neutral" {
  if (!task.expected) return "neutral";
  return taskHealthy(task) ? "success" : "danger";
}

function statusTone(status: "pass" | "fail" | "not_run"): "success" | "danger" | "warning" {
  return status === "pass" ? "success" : status === "fail" ? "danger" : "warning";
}

watch(() => props.deviceId, () => {
  stopPolling();
  health.value = undefined;
  audioSession.value = undefined;
  selfTest.value = undefined;
  void loadHealth(true);
});

onMounted(() => void loadHealth(true));
onBeforeUnmount(stopPolling);
</script>

<template>
  <section class="device-diagnostics" aria-labelledby="device-diagnostics-title">
    <header class="diagnostics-heading">
      <div>
        <span class="diagnostics-kicker">DEVICE OBSERVABILITY / P0</span>
        <h2 id="device-diagnostics-title">Chẩn đoán thiết bị</h2>
        <p>Đo tín hiệu và sức khỏe runtime ngay trên phiên hiện tại, không đụng vào Wi-Fi hay NVS.</p>
      </div>
      <VtButton size="sm" variant="secondary" :busy="refreshing" @click="loadHealth()">
        <VtIcon name="refresh" :size="15" /> Làm mới
      </VtButton>
    </header>

    <div class="diagnostics-privacy">
      <VtIcon name="mic" :size="17" />
      <span><b>Audio debugger có kiểm soát.</b> Chỉ tính metrics trong RAM; raw audio không được lưu hoặc truyền.</span>
    </div>

    <div v-if="loading" class="diagnostics-state" role="status">
      <span class="diagnostics-spinner"></span><b>Đang đọc health từ thiết bị…</b>
    </div>
    <div v-else-if="error && !health" class="diagnostics-state is-error" role="alert">
      <VtIcon name="warning" :size="19" /><div><b>Thiết bị chưa phản hồi MCP.</b><p>{{ error }}</p><VtButton size="sm" @click="loadHealth(true)">Thử lại</VtButton></div>
    </div>
    <template v-else-if="health">
      <div class="diagnostics-summary">
        <article><span>RUNTIME</span><strong :class="{ good: softwareHealthy }">{{ softwareHealthy ? "Ổn định" : "Cần kiểm tra" }}</strong><small>{{ health.device.state }} · {{ health.device.firmwareVersion }}</small></article>
        <article><span>UPTIME</span><strong>{{ formatNumber(health.device.uptimeMs / 1000 / 60) }}′</strong><small>reset: {{ health.device.resetReason }}</small></article>
        <article><span>WI-FI</span><strong>{{ health.network.connected ? `${health.network.rssi} dBm` : "Offline" }}</strong><small>{{ health.network.ipv4 || "Chưa có IPv4" }}</small></article>
        <article><span>WAKE / UI</span><strong :class="{ good: health.resources.wakeResourceHealthy && health.resources.uiPackHealthy }">{{ health.resources.wakeResourceHealthy && health.resources.uiPackHealthy ? "Ready" : "Degraded" }}</strong><small>{{ health.resources.wakeDroppedFrames }} frame wake bị drop</small></article>
      </div>

      <div class="diagnostics-grid">
        <article class="diagnostics-card">
          <header><div><span class="card-kicker">MEMORY</span><h3>Bộ nhớ runtime</h3></div><VtBadge tone="info">ESP32-S3</VtBadge></header>
          <dl><div><dt>Internal free</dt><dd>{{ formatNumber(health.memory.internalFreeBytes / 1024) }} KB</dd></div><div><dt>Internal min</dt><dd>{{ formatNumber(health.memory.internalMinFreeBytes / 1024) }} KB</dd></div><div><dt>PSRAM free</dt><dd>{{ formatNumber(health.memory.psramFreeBytes / 1024) }} KB</dd></div><div><dt>PSRAM min</dt><dd>{{ formatNumber(health.memory.psramMinFreeBytes / 1024) }} KB</dd></div></dl>
        </article>
        <article class="diagnostics-card">
          <header><div><span class="card-kicker">NETWORK</span><h3>Kết nối hiện tại</h3></div><VtBadge :tone="health.network.connected ? 'success' : 'danger'" dot>{{ health.network.connected ? "Connected" : "Offline" }}</VtBadge></header>
          <dl><div><dt>IPv4</dt><dd>{{ health.network.ipv4 || "—" }}</dd></div><div><dt>RSSI</dt><dd>{{ health.network.connected ? `${health.network.rssi} dBm` : "—" }}</dd></div><div><dt>Disconnects</dt><dd>{{ health.network.disconnectCount }}</dd></div><div><dt>Reconnect attempts</dt><dd>{{ health.network.reconnectAttemptCount }}</dd></div></dl>
        </article>
        <article class="diagnostics-card diagnostics-card-wide">
          <header><div><span class="card-kicker">AUDIO PIPELINE</span><h3>Capture / playback</h3></div><VtBadge :tone="health.audio.captureTaskRunning && health.audio.playbackTaskRunning ? 'success' : 'danger'" dot>{{ health.audio.captureTaskRunning && health.audio.playbackTaskRunning ? "Tasks alive" : "Task fault" }}</VtBadge></header>
          <div class="audio-facts"><div><b>{{ formatNumber(health.audio.lifetime.micFrames) }}</b><span>mic frames</span></div><div><b>{{ formatNumber(health.audio.lifetime.detectorFrameDrops) }}</b><span>detector drops</span></div><div><b>{{ formatNumber(health.audio.lifetime.opusEncodeFailures + health.audio.lifetime.opusDecodeFailures) }}</b><span>Opus errors</span></div><div><b>{{ formatNumber(health.audio.lifetime.playbackQueueDrops) }}</b><span>queue drops</span></div></div>
        </article>
        <article class="diagnostics-card diagnostics-card-wide task-headroom-card">
          <header>
            <div><span class="card-kicker">STACK HEADROOM</span><h3>Biên an toàn của task realtime</h3></div>
            <VtBadge
              v-if="health.tasks"
              :tone="stackHeadroomHealthy ? 'success' : 'danger'"
              dot
            >{{ stackHeadroomHealthy ? "An toàn" : "Cần xử lý" }}</VtBadge>
            <VtBadge v-else tone="neutral">Firmware cũ</VtBadge>
          </header>
          <template v-if="health.tasks">
            <div class="task-headroom-grid">
              <div v-for="row in taskHeadroomRows" :key="row.id" class="task-headroom-row">
                <span>
                  <b>{{ row.label }}</b>
                  <small>{{ row.task.expected ? "Được yêu cầu trong runtime" : "Không kích hoạt trong profile" }}</small>
                </span>
                <strong>{{ row.task.running ? `${formatNumber(row.task.stackFreeBytes / 1024)} KB` : "—" }}</strong>
                <VtBadge :tone="taskStatusTone(row.task)" dot>{{ taskStatus(row.task) }}</VtBadge>
              </div>
            </div>
            <p class="task-headroom-note">
              Cảnh báo khi một task được yêu cầu bị dừng hoặc từng còn dưới
              {{ formatNumber(health.tasks.minimumStackFreeBytes / 1024) }} KB stack.
              WebSocket ở đây là task điều phối của Veetee.
            </p>
          </template>
          <div v-else class="diagnostics-empty">Firmware hiện tại chưa gửi task headroom; các health metric khác vẫn dùng được.</div>
        </article>
      </div>

      <article class="diagnostics-card self-test-card">
        <header><div><span class="card-kicker">NON-DESTRUCTIVE CHECK</span><h3>Self-test từ Manager</h3><p>Chạy snapshot tức thời; không phát tone, không reconnect mạng và không sửa cấu hình.</p></div><VtButton size="sm" :busy="selfTestBusy" @click="runSelfTest"><VtIcon name="play" :size="14" /> Chạy self-test</VtButton></header>
        <p v-if="selfTestError" class="diagnostics-inline-error" role="alert">{{ selfTestError }}</p>
        <div v-if="selfTest" class="self-test-results">
          <div class="self-test-result-heading"><VtBadge :tone="selfTest.overall === 'pass' ? 'success' : 'danger'" dot>{{ selfTest.overall === "pass" ? "Software checks pass" : "Có check thất bại" }}</VtBadge><small>uptime {{ formatNumber(selfTest.runAtUptimeMs / 1000) }}s</small></div>
          <div class="self-test-checks"><div v-for="check in selfTest.checks" :key="check.id"><VtBadge :tone="statusTone(check.status)">{{ check.status === "pass" ? "PASS" : check.status === "fail" ? "FAIL" : "N/A" }}</VtBadge><span><b>{{ check.id }}</b><small>{{ check.detail }}<em v-if="check.requiresListener"> · cần người nghe</em></small></span></div></div>
        </div>
        <div v-else class="diagnostics-empty">Chưa có lần self-test nào trong phiên này.</div>
      </article>

      <article class="diagnostics-card audio-debug-card">
        <header><div><span class="card-kicker">METRICS-ONLY SESSION</span><h3>Audio debugger</h3><p>Chọn thời lượng ngắn để đo noise floor, clipping và frame path hiện tại.</p></div><VtBadge :tone="isRunning ? 'warning' : diagnostic?.state === 'completed' ? 'success' : 'neutral'" dot>{{ isRunning ? `Đang đo · ${remainingSeconds}s` : diagnostic?.state === "completed" ? "Đã hoàn tất" : "Chưa chạy" }}</VtBadge></header>
        <div class="audio-debug-controls"><label>Thời lượng <VtSelect v-model="duration" :disabled="isRunning"><option value="3">3 giây</option><option value="5">5 giây</option><option value="10">10 giây</option></VtSelect></label><VtButton size="sm" :busy="audioBusy" :disabled="isRunning" @click="startAudioDiagnostic"><VtIcon :name="isRunning ? 'stop' : 'mic'" :size="15" /> {{ isRunning ? "Đang đo…" : "Bắt đầu đo" }}</VtButton></div>
        <p v-if="error && health" class="diagnostics-inline-error" role="alert">{{ error }}</p>
        <div v-if="diagnostic?.state === 'completed'" class="audio-results"><div><span>RMS</span><b>{{ formatNumber(diagnostic.rms) }}</b></div><div><span>Peak</span><b>{{ formatNumber(diagnostic.peakAbsolute) }}</b></div><div><span>DC offset</span><b>{{ formatNumber(diagnostic.dcOffset) }}</b></div><div><span>Clipping</span><b>{{ formatNumber(diagnostic.clippingPercent) }}%</b></div><small>{{ formatNumber(diagnostic.sampleCount) }} samples · {{ diagnostic.counters.micReadTimeouts }} timeout · {{ diagnostic.counters.uplinkDrops }} uplink drop</small></div>
      </article>
    </template>
  </section>
</template>

<style scoped>
.device-diagnostics { display: grid; gap: 16px; min-width: 0; }
.diagnostics-heading, .diagnostics-card > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 15px; }
.diagnostics-kicker, .card-kicker { color: var(--muted); font-size: 8px; font-weight: 800; letter-spacing: .16em; }
.diagnostics-heading h2 { margin: 5px 0; font-size: clamp(22px, 3vw, 32px); }
.diagnostics-heading p, .diagnostics-card p { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.55; }
.diagnostics-privacy { display: flex; align-items: center; gap: 9px; border: 1px solid rgba(24,116,94,.2); border-radius: 13px; padding: 11px 13px; color: var(--success); background: #edf7f2; font-size: 10px; }
.diagnostics-privacy b { color: var(--ink); }
.diagnostics-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; }
.diagnostics-summary article { display: grid; gap: 5px; min-width: 0; border: 1px solid var(--line); border-radius: 15px; padding: 14px; background: var(--paper); box-shadow: var(--shadow-sm); }
.diagnostics-summary span { color: var(--muted); font-size: 8px; font-weight: 800; letter-spacing: .12em; }
.diagnostics-summary strong { overflow: hidden; color: var(--danger); font-size: 20px; text-overflow: ellipsis; white-space: nowrap; }
.diagnostics-summary strong.good { color: var(--success); }
.diagnostics-summary small { overflow: hidden; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.diagnostics-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.diagnostics-card { display: grid; gap: 16px; border: 1px solid var(--line); border-radius: 18px; padding: 18px; background: var(--paper); box-shadow: var(--shadow-sm); }
.diagnostics-card-wide { grid-column: 1 / -1; }
.diagnostics-card h3 { margin: 4px 0 0; font-size: 16px; }
.diagnostics-card dl { display: grid; gap: 0; margin: 0; border: 1px solid var(--line); border-radius: 11px; overflow: hidden; }
.diagnostics-card dl div { display: flex; justify-content: space-between; gap: 12px; border-top: 1px solid var(--line); padding: 9px 11px; }
.diagnostics-card dl div:first-child { border-top: 0; }
.diagnostics-card dt { color: var(--muted); font-size: 9px; }
.diagnostics-card dd { margin: 0; font-size: 10px; font-weight: 800; text-align: right; }
.audio-facts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.audio-facts div, .audio-results div { display: grid; gap: 4px; border-radius: 11px; padding: 11px; background: #edf1ed; }
.audio-facts b { font-size: 20px; }
.audio-facts span, .audio-results span { color: var(--muted); font-size: 8px; }
.task-headroom-card { gap: 12px; }
.task-headroom-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.task-headroom-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 10px; min-width: 0; border: 1px solid var(--line); border-radius: 12px; padding: 11px; background: #f7f8f4; }
.task-headroom-row > span { display: grid; min-width: 0; gap: 3px; }
.task-headroom-row b { overflow: hidden; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.task-headroom-row small { color: var(--muted); font-size: 8px; }
.task-headroom-row strong { font-size: 13px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.task-headroom-note { border-left: 3px solid var(--navy-2); padding-left: 10px; }
.self-test-card, .audio-debug-card { gap: 14px; }
.self-test-card > header > div, .audio-debug-card > header > div { display: grid; gap: 4px; min-width: 0; }
.self-test-card > header p, .audio-debug-card > header p { margin-top: 3px; }
.self-test-results { display: grid; gap: 11px; }
.self-test-result-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.self-test-result-heading small { color: var(--muted); font-size: 9px; }
.self-test-checks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
.self-test-checks > div { display: flex; align-items: flex-start; gap: 8px; min-width: 0; border: 1px solid var(--line); border-radius: 11px; padding: 9px; }
.self-test-checks span { display: grid; min-width: 0; gap: 3px; }
.self-test-checks b { font-size: 9px; }
.self-test-checks small { color: var(--muted); font-size: 8px; line-height: 1.4; }
.self-test-checks em { color: var(--warning); font-style: normal; }
.diagnostics-empty { border: 1px dashed var(--line-strong); border-radius: 11px; padding: 15px; color: var(--muted); font-size: 9px; text-align: center; }
.audio-debug-controls { display: flex; align-items: end; gap: 10px; }
.audio-debug-controls label { display: grid; gap: 5px; color: var(--muted); font-size: 9px; font-weight: 700; }
.audio-debug-controls .vt-select { min-width: 125px; }
.audio-results { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.audio-results b { font-size: 19px; }
.audio-results small { grid-column: 1 / -1; color: var(--muted); font-size: 8px; }
.diagnostics-inline-error { border-radius: 10px; padding: 9px 11px; color: var(--danger) !important; background: #fff0ed; }
.diagnostics-state { display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 180px; border: 1px dashed var(--line-strong); border-radius: 16px; color: var(--muted); }
.diagnostics-state.is-error { justify-content: flex-start; padding: 20px; color: var(--danger); background: #fff9f7; }
.diagnostics-state.is-error div { display: grid; gap: 7px; }
.diagnostics-state p { margin: 0; color: var(--muted); font-size: 9px; }
.diagnostics-spinner { width: 18px; height: 18px; border: 2px solid var(--line); border-top-color: var(--navy-2); border-radius: 50%; animation: diagnostics-spin .7s linear infinite; }
@keyframes diagnostics-spin { to { transform: rotate(360deg); } }
@media (max-width: 800px) { .diagnostics-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); } .diagnostics-grid { grid-template-columns: 1fr; } .diagnostics-card-wide { grid-column: auto; } }
@media (max-width: 560px) { .diagnostics-heading, .diagnostics-card > header { flex-direction: column; align-items: stretch; } .diagnostics-heading .vt-button, .diagnostics-card > header .vt-button { align-self: flex-start; } .audio-facts, .audio-results, .self-test-checks, .task-headroom-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .task-headroom-row { grid-template-columns: minmax(0, 1fr) auto; } .task-headroom-row .vt-badge { grid-column: 1 / -1; justify-self: start; } .audio-debug-controls { align-items: stretch; flex-direction: column; } .audio-debug-controls .vt-button { align-self: flex-start; } }
@media (max-width: 390px) { .task-headroom-grid { grid-template-columns: 1fr; } }
</style>
