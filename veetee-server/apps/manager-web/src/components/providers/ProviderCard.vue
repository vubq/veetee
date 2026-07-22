<script setup lang="ts">
import type { Provider } from "../../api/schemas";
import { formatDate, statusTone } from "../../utils/format";
import { VtBadge, VtButton, VtIcon } from "../ui";

defineProps<{
  provider: Provider;
  testing: boolean;
}>();

const emit = defineEmits<{
  test: [];
  edit: [];
}>();

const kindLabels: Record<Provider["kind"], string> = {
  vad: "Phát hiện giọng nói",
  asr: "Nhận dạng tiếng nói",
  llm: "Mô hình ngôn ngữ",
  tts: "Tổng hợp giọng nói",
  realtime: "Realtime speech",
  memory: "Bộ nhớ",
};

const healthLabels: Record<Provider["health"], string> = {
  healthy: "Khỏe",
  degraded: "Cần kiểm tra",
  unknown: "Chưa đo",
};

const errorMessages: Record<string, string> = {
  runtime_probe_unavailable: "Bản ghi cũ chưa có phép đo runtime. Test lại để đọc trạng thái từ Voice Server.",
  voice_runtime_unreachable: "Manager API chưa kết nối được Voice Server nội bộ.",
  runtime_component_unreported: "Voice Server chưa công bố component tương ứng trong readiness.",
  runtime_component_unhealthy: "Component đã nạp nhưng readiness đang báo chưa khỏe.",
  timeout: "Runtime phản hồi quá thời gian cho phép.",
  unreachable: "Không thể kết nối endpoint đã cấu hình.",
};

function healthDescription(provider: Provider): string {
  if (!provider.enabled) return "Provider đang tắt nên không tham gia routing.";
  if (provider.health === "healthy") return "Runtime phản hồi bình thường và sẵn sàng tham gia routing.";
  if (provider.healthErrorCode?.startsWith("http_")) {
    return `Endpoint trả về HTTP ${provider.healthErrorCode.slice(5)}.`;
  }
  if (provider.healthErrorCode) return errorMessages[provider.healthErrorCode] ?? "Phép kiểm tra runtime chưa thành công.";
  return provider.health === "unknown"
    ? "Chưa có kết quả kiểm tra. Nhấn Test để đo runtime hiện tại."
    : "Runtime đang giảm chất lượng; kiểm tra endpoint và log dịch vụ.";
}

function circuitLabel(value: Provider["circuitState"]): string {
  if (value === "closed") return "Đóng · cho phép route";
  if (value === "half_open") return "Thử phục hồi";
  return "Mở · tạm ngắt route";
}

function authLabel(provider: Provider): string {
  if (!provider.baseUrl) return "Nội bộ tiến trình";
  return provider.secretConfigured ? "Bearer secret" : "Không dùng secret";
}
</script>

<template>
  <article class="provider-card" :class="{ 'is-disabled': !provider.enabled }" :data-provider-kind="provider.kind">
    <header>
      <span class="provider-kind-icon"><VtIcon name="provider" :size="20" /></span>
      <div class="provider-identity">
        <span class="vt-kicker">{{ kindLabels[provider.kind] }} · P{{ provider.priority }}</span>
        <h2>{{ provider.model }}</h2>
        <p>{{ provider.adapter }}</p>
      </div>
      <div class="provider-badges">
        <VtBadge :tone="provider.enabled ? 'info' : 'neutral'">{{ provider.enabled ? "Đang bật" : "Đã tắt" }}</VtBadge>
        <VtBadge :tone="statusTone(provider.health)" dot>{{ healthLabels[provider.health] }}</VtBadge>
      </div>
    </header>

    <div class="provider-runtime">
      <span><VtIcon :name="provider.baseUrl ? 'telemetry' : 'device'" :size="16" /></span>
      <div>
        <small>{{ provider.baseUrl ? "HTTP RUNTIME" : "VOICE SERVER RUNTIME" }}</small>
        <code>{{ provider.baseUrl ?? "In-process · kiểm tra qua /health/ready" }}</code>
      </div>
    </div>

    <dl class="provider-facts">
      <div><dt>Độ trễ probe</dt><dd>{{ provider.healthLatencyMs !== undefined ? `${provider.healthLatencyMs} ms` : "Chưa đo" }}</dd></div>
      <div><dt>Circuit breaker</dt><dd>{{ circuitLabel(provider.circuitState) }}</dd></div>
      <div><dt>Ngôn ngữ</dt><dd>{{ provider.locales.join(", ") || "—" }}</dd></div>
      <div><dt>Xác thực</dt><dd>{{ authLabel(provider) }}</dd></div>
    </dl>

    <div class="provider-health-note" :class="`is-${provider.health}`">
      <span><VtIcon :name="provider.health === 'healthy' ? 'check' : 'warning'" :size="16" /></span>
      <div><b>{{ healthLabels[provider.health] }}</b><p>{{ healthDescription(provider) }}</p></div>
    </div>

    <footer>
      <small>{{ provider.healthCheckedAt ? `Kiểm tra ${formatDate(provider.healthCheckedAt)}` : "Chưa kiểm tra kết nối" }}</small>
      <div>
        <VtButton size="sm" variant="secondary" :busy="testing" @click="emit('test')"><VtIcon name="refresh" :size="15" /> Test runtime</VtButton>
        <VtButton size="sm" variant="quiet" @click="emit('edit')"><VtIcon name="edit" :size="15" /> Cấu hình</VtButton>
      </div>
    </footer>
  </article>
</template>
