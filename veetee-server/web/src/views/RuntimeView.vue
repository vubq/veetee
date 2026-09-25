<script setup>
import { computed } from 'vue'
import {
  Braces,
  CheckCircle2,
  Gauge,
  KeyRound,
  Plus,
  Save,
  ServerCog,
  ShieldCheck,
  Trash2,
} from '@lucide/vue'
import PageHeader from '../components/layout/PageHeader.vue'
import UiBadge from '../components/ui/UiBadge.vue'
import UiButton from '../components/ui/UiButton.vue'
import UiInput from '../components/ui/UiInput.vue'
import UiSegmented from '../components/ui/UiSegmented.vue'
import UiSelect from '../components/ui/UiSelect.vue'
import UiSwitch from '../components/ui/UiSwitch.vue'

const props = defineProps({
  draft: { type: Object, required: true },
  runtime: { type: Object, default: () => ({}) },
  groqKeys: { type: Array, default: () => [] },
  models: { type: Array, default: () => [] },
  voices: { type: Array, default: () => [] },
  busy: Boolean,
  secretPlaceholder: { type: Function, required: true },
})

const emit = defineEmits([
  'update',
  'add-groq-key',
  'update-groq-key',
  'update-groq-limit',
  'remove-groq-key',
  'save',
])

const modelOptions = () => props.models.map(item => ({ value: item, label: item }))
const voiceOptions = () => props.voices.map(item => ({
  value: item.name,
  label: item.name,
  description: item.description || '',
}))

const hfConfigured = computed(() => Boolean(props.runtime?.HF_TOKEN?.configured))
const groqConfiguredCount = computed(() => props.groqKeys.filter(item => item.configured).length)

function setValue(key, value) {
  emit('update', key, value)
}

function formatTokenCount(value) {
  const number = Number(value || 0)
  return new Intl.NumberFormat('vi-VN').format(
    Number.isFinite(number) ? Math.max(0, number) : 0,
  )
}

function tokenLimitLabel(item) {
  const limit = Number(item.tokenLimit || 0)
  return limit > 0 ? formatTokenCount(limit) : 'Không giới hạn'
}

function tokenUsagePercent(item) {
  const limit = Number(item.tokenLimit || 0)
  const used = Number(item.usedTokens || 0)
  if (!Number.isFinite(limit) || limit <= 0) return 0
  return Math.min(100, Math.max(0, (used / limit) * 100))
}

function remainingTokenLabel(item) {
  const limit = Number(item.tokenLimit || 0)
  if (!Number.isFinite(limit) || limit <= 0) return '∞'
  return formatTokenCount(Math.max(0, limit - Number(item.usedTokens || 0)))
}
</script>

<template>
  <div class="workspace-view runtime-view">
    <PageHeader
      title="Runtime & Credentials"
      description="Quản lý provider, model và secret. Mọi thay đổi hỗ trợ đều được áp dụng trực tiếp khi lưu, không restart service."
    >
      <template #actions>
        <UiBadge tone="success" dot>Hot apply</UiBadge>
        <UiButton variant="primary" :loading="busy" @click="emit('save')">
          <template #icon><Save :size="16" /></template>
          Lưu thay đổi
        </UiButton>
      </template>
    </PageHeader>

    <div class="runtime-provider-grid">
      <section class="section-card provider-card provider-card--groq">
        <header class="section-card__header">
          <div class="section-card__title-with-icon">
            <span><Braces :size="18" /></span>
            <div>
              <h2>LLM · Groq</h2>
              <p>Pool nhiều key để chia quota và failover. Có thể thêm bao nhiêu key tùy nhu cầu.</p>
            </div>
          </div>
          <UiBadge :tone="groqConfiguredCount ? 'success' : 'warning'">
            {{ groqConfiguredCount }} configured
          </UiBadge>
        </header>

        <div class="groq-key-list">
          <div v-for="(item, index) in groqKeys" :key="item.envKey" class="groq-key-card">
            <div class="groq-key-card__header">
              <div class="groq-key-card__identity">
                <span class="groq-key-card__index">{{ index + 1 }}</span>
                <div>
                  <strong>Groq API key {{ index + 1 }}</strong>
                  <small>{{ item.envKey }}</small>
                </div>
              </div>
              <div class="groq-key-card__actions">
                <UiBadge v-if="item.configured" tone="success" dot>Đã lưu</UiBadge>
                <UiBadge v-else tone="neutral">Mới</UiBadge>
                <UiButton
                  variant="danger-ghost"
                  size="sm"
                  icon-only
                  :aria-label="`Xóa Groq key ${index + 1}`"
                  title="Xóa key"
                  @click="emit('remove-groq-key', item.envKey)"
                >
                  <template #icon><Trash2 :size="15" /></template>
                </UiButton>
              </div>
            </div>

            <div class="groq-key-card__fields">
              <UiInput
                :model-value="item.value"
                label="API key"
                type="password"
                autocomplete="new-password"
                :placeholder="item.configured ? (item.masked || 'Đã cấu hình') : 'gsk_…'"
                class="mono-field"
                @update:model-value="emit('update-groq-key', item.envKey, $event)"
              />
              <UiInput
                :model-value="item.tokenLimit"
                label="Giới hạn token / ngày"
                type="number"
                min="0"
                step="1000"
                placeholder="200000"
                hint="0 = không giới hạn"
                @update:model-value="emit('update-groq-limit', item.envKey, $event)"
              />
            </div>

            <div class="groq-key-card__usage">
              <div class="groq-key-card__stat">
                <span>Đã dùng hôm nay</span>
                <strong>{{ formatTokenCount(item.usedTokens) }}</strong>
              </div>
              <div class="groq-key-card__stat">
                <span>Còn lại</span>
                <strong>{{ remainingTokenLabel(item) }}</strong>
              </div>
              <div class="groq-key-card__stat">
                <span>Giới hạn/ngày</span>
                <strong>{{ tokenLimitLabel(item) }}</strong>
              </div>
              <div class="groq-key-card__progress" aria-hidden="true">
                <span :style="{ width: `${tokenUsagePercent(item)}%` }"></span>
              </div>
            </div>
          </div>

          <button type="button" class="groq-add-key" @click="emit('add-groq-key')">
            <span><Plus :size="16" /></span>
            <div>
              <strong>Thêm Groq API key</strong>
              <small>Key mới sẽ tự tham gia pool sau khi lưu.</small>
            </div>
          </button>
        </div>

        <footer class="section-card__footer-note">
          <ShieldCheck :size="15" />
          <span>Key được mask sau khi lưu. Khi pool thay đổi, LLM được probe và hot-reload trước khi cấu hình mới có hiệu lực.</span>
        </footer>
      </section>

      <div class="runtime-provider-side">
        <section class="section-card provider-card">
          <header class="section-card__header">
            <div class="section-card__title-with-icon">
              <span><KeyRound :size="18" /></span>
              <div>
                <h2>Models · Hugging Face</h2>
                <p>Parakeet/NeMo dùng Hugging Face Hub để tải checkpoint khi cache local chưa có. Token là tùy chọn để tăng rate limit.</p>
              </div>
            </div>
            <UiBadge :tone="hfConfigured ? 'success' : 'neutral'" dot>
              {{ hfConfigured ? 'Configured' : 'Optional' }}
            </UiBadge>
          </header>

          <div class="provider-card__body">
            <UiInput
              :model-value="draft.HF_TOKEN"
              label="Hugging Face token"
              type="password"
              autocomplete="new-password"
              :placeholder="secretPlaceholder('HF_TOKEN')"
              class="mono-field"
              @update:model-value="setValue('HF_TOKEN', $event)"
            />
          </div>
        </section>
      </div>
    </div>

    <section class="section-card runtime-defaults-card">
      <header class="section-card__header">
        <div class="section-card__title-with-icon">
          <span><ServerCog :size="18" /></span>
          <div>
            <h2>Runtime defaults</h2>
            <p>Global fallback khi Assistant không có model hoặc voice override riêng.</p>
          </div>
        </div>
        <div class="runtime-hot-note"><CheckCircle2 :size="15" />Áp dụng ngay khi lưu</div>
      </header>

      <div class="runtime-defaults-grid">
        <UiSelect
          :model-value="draft['llm.model']"
          label="LLM model"
          :options="modelOptions()"
          searchable
          placeholder="Chọn model"
          @update:model-value="setValue('llm.model', $event)"
        />

        <UiSelect
          :model-value="draft['tts.voice']"
          label="TTS voice"
          :options="voiceOptions()"
          searchable
          placeholder="Chọn giọng"
          @update:model-value="setValue('tts.voice', $event)"
        />

        <div class="runtime-setting-row">
          <div>
            <strong>ASR device</strong>
            <span>Đổi CPU/CUDA sẽ preload backend mới rồi swap vào session đang chạy.</span>
          </div>
          <UiSegmented
            :model-value="draft['asr.device']"
            :options="[{ value: 'cuda', label: 'CUDA' }, { value: 'cpu', label: 'CPU' }]"
            @update:model-value="setValue('asr.device', $event)"
          />
        </div>

        <div class="runtime-setting-row">
          <div>
            <strong>Barge-in policy</strong>
            <span>Firmware/client hiện là bên điều khiển barge-in; server-side automatic chưa được bật.</span>
          </div>
          <UiBadge tone="neutral">Client only</UiBadge>
        </div>
      </div>
    </section>

    <section class="section-card runtime-defaults-card">
      <header class="section-card__header">
        <div class="section-card__title-with-icon">
          <span><ServerCog :size="18" /></span>
          <div>
            <h2>Latency & context tuning</h2>
            <p>Mục tiêu warm path là khoảng 600 ms tới audio đầu. Timeout được để rộng hơn để không biến jitter mạng thành lỗi.</p>
          </div>
        </div>
        <div class="runtime-hot-note"><CheckCircle2 :size="15" />Hot apply</div>
      </header>

      <div class="runtime-defaults-grid">
        <UiInput
          :model-value="draft['latency.target_first_audio_ms']"
          label="Target first audio"
          type="number"
          min="100"
          max="5000"
          step="50"
          suffix="ms"
          hint="SLO để theo dõi, không phải timeout cứng."
          @update:model-value="setValue('latency.target_first_audio_ms', $event)"
        />
        <UiInput
          :model-value="draft['latency.first_token_timeout_ms']"
          label="First token timeout"
          type="number"
          min="100"
          max="30000"
          step="100"
          suffix="ms"
          hint="Phải lớn hơn hoặc bằng target."
          @update:model-value="setValue('latency.first_token_timeout_ms', $event)"
        />
        <UiInput
          :model-value="draft['latency.total_turn_timeout_ms']"
          label="Total turn timeout"
          type="number"
          min="500"
          max="120000"
          step="500"
          suffix="ms"
          @update:model-value="setValue('latency.total_turn_timeout_ms', $event)"
        />
        <UiInput
          :model-value="draft['llm.routing.headroom_pct']"
          label="LLM quota headroom"
          type="number"
          min="0"
          max="90"
          step="1"
          suffix="%"
          hint="Giữ khoảng trống quota khi reserve token; không thay đổi semantic/model."
          @update:model-value="setValue('llm.routing.headroom_pct', $event)"
        />
        <UiInput
          :model-value="draft['llm.routing.max_attempts']"
          label="LLM route attempts"
          type="number"
          min="1"
          max="16"
          step="1"
          hint="Số quota-group tối đa cho một request khi 429/failover."
          @update:model-value="setValue('llm.routing.max_attempts', $event)"
        />
        <UiInput
          :model-value="draft['llm.routing.admission_wait_ms']"
          label="LLM admission wait"
          type="number"
          min="0"
          max="5000"
          step="10"
          suffix="ms"
          hint="Wait ngắn khi quota đã biết đang bận; quota cạn thật vẫn fail nhanh."
          @update:model-value="setValue('llm.routing.admission_wait_ms', $event)"
        />
        <UiInput
          :model-value="draft['llm.routing.discovery_wait_ms']"
          label="LLM discovery wait"
          type="number"
          min="0"
          max="5000"
          step="50"
          suffix="ms"
          hint="Chỉ dùng khi failover gặp quota-group chưa học headers và đang có discovery request in-flight."
          @update:model-value="setValue('llm.routing.discovery_wait_ms', $event)"
        />
        <UiInput
          :model-value="draft['llm.routing.discovery_max_inflight']"
          label="Quota discovery inflight"
          type="number"
          min="1"
          max="64"
          step="1"
          hint="Giới hạn request song song trước khi biết quota thật của group."
          @update:model-value="setValue('llm.routing.discovery_max_inflight', $event)"
        />
        <UiInput
          :model-value="draft['llm.routing.inflight_penalty_s']"
          label="Route inflight penalty"
          type="number"
          min="0"
          max="10"
          step="0.1"
          suffix="s"
          hint="Phạt route đang bận để phân tán request sang key/group khác."
          @update:model-value="setValue('llm.routing.inflight_penalty_s', $event)"
        />
        <UiInput
          :model-value="draft['llm.routing.latency_ewma_alpha']"
          label="Route latency EWMA"
          type="number"
          min="0"
          max="1"
          step="0.05"
          hint="Độ nhạy khi học first-event latency của từng quota-group."
          @update:model-value="setValue('llm.routing.latency_ewma_alpha', $event)"
        />
        <UiInput
          :model-value="draft['llm.routing.latency_jitter_penalty']"
          label="Route jitter penalty"
          type="number"
          min="0"
          max="10"
          step="0.05"
          hint="Tránh chọn key nhanh trung bình nhưng p95/jitter xấu."
          @update:model-value="setValue('llm.routing.latency_jitter_penalty', $event)"
        />
        <UiInput
          :model-value="draft['asr.min_silence_duration_ms']"
          label="Local VAD silence"
          type="number"
          min="96"
          max="2000"
          step="32"
          suffix="ms"
          hint="Điểm kết thúc câu của Parakeet/Silero. Giảm để phản hồi sớm hơn; A/B bằng corpus trước khi chọn mặc định."
          @update:model-value="setValue('asr.min_silence_duration_ms', $event)"
        />
        <UiSwitch
          :model-value="Boolean(draft['asr.speculative_inference_enabled'])"
          label="Speculative local ASR"
          description="Chạy Parakeet sớm trong trailing silence và chỉ reuse transcript confidence cao. Không suy intent, không keyword routing."
          @update:model-value="setValue('asr.speculative_inference_enabled', $event)"
        />
        <UiInput
          :model-value="draft['asr.speculative_start_silence_ms']"
          label="Speculative start silence"
          type="number"
          min="32"
          max="1000"
          step="32"
          suffix="ms"
          hint="Phải nhỏ hơn Local VAD silence. Giá trị thấp overlap inference sớm hơn nhưng tăng GPU work khi người dùng ngập ngừng."
          @update:model-value="setValue('asr.speculative_start_silence_ms', $event)"
        />
        <UiInput
          :model-value="draft['asr.speculative_min_confidence']"
          label="Speculative min confidence"
          type="number"
          min="0"
          max="1"
          step="0.01"
          hint="Chỉ reuse snapshot nếu confidence acoustic tối thiểu đạt ngưỡng; thấp hơn sẽ fallback final inference."
          @update:model-value="setValue('asr.speculative_min_confidence', $event)"
        />
        <UiSwitch
          :model-value="Boolean(draft['asr.speculative_llm_enabled'])"
          label="Speculative AI turn"
          description="Bắt đầu cùng AI/tool turn trong trailing silence nhưng chỉ buffer event. Không phát TTS, không execute tool và không ghi memory cho tới khi ASR final xác nhận đúng transcript."
          @update:model-value="setValue('asr.speculative_llm_enabled', $event)"
        />
        <UiInput
          :model-value="draft['asr.speculative_llm_min_confidence']"
          label="Speculative AI confidence"
          type="number"
          min="0"
          max="1"
          step="0.01"
          hint="Confidence tối thiểu để cho phép gửi speculative AI request. Nên >= speculative ASR confidence."
          @update:model-value="setValue('asr.speculative_llm_min_confidence', $event)"
        />
        <UiInput
          :model-value="draft['asr.vad_threshold']"
          label="VAD voice threshold"
          type="number"
          min="0.05"
          max="0.99"
          step="0.05"
          hint="Ngưỡng bắt đầu/giữ speech của Silero. Chỉ tune bằng corpus có tiếng ồn và câu ngập ngừng."
          @update:model-value="setValue('asr.vad_threshold', $event)"
        />
        <UiInput
          :model-value="draft['asr.vad_threshold_low']"
          label="VAD silence threshold"
          type="number"
          min="0.01"
          max="0.95"
          step="0.05"
          hint="Phải nhỏ hơn voice threshold. Ảnh hưởng thời điểm Silero chuyển về silence."
          @update:model-value="setValue('asr.vad_threshold_low', $event)"
        />
        <UiInput
          :model-value="draft['asr.vad_end_threshold']"
          label="VAD end threshold"
          type="number"
          min="0.01"
          max="0.99"
          step="0.05"
          hint="Chỉ dùng sau khi speech đã bắt đầu. Tăng có thể cắt đuôi Silero sớm hơn mà không làm khó speech-start; phải A/B WER/false-cut trước khi đổi mặc định."
          @update:model-value="setValue('asr.vad_end_threshold', $event)"
        />
        <UiInput
          :model-value="draft['asr.speech_start_frames']"
          label="Speech start frames"
          type="number"
          min="1"
          max="8"
          step="1"
          suffix="×32ms"
          hint="Số frame voice liên tiếp để xác nhận bắt đầu câu; thấp hơn nhạy hơn với nhiễu."
          @update:model-value="setValue('asr.speech_start_frames', $event)"
        />
        <UiInput
          :model-value="draft['asr.pre_speech_pad_ms']"
          label="ASR pre-roll"
          type="number"
          min="0"
          max="2000"
          step="32"
          suffix="ms"
          hint="Âm thanh giữ trước speech-start để không mất âm đầu. Giảm có thể hạ inference nhưng cần kiểm WER."
          @update:model-value="setValue('asr.pre_speech_pad_ms', $event)"
        />
        <UiInput
          :model-value="draft['asr.min_speech_duration_ms']"
          label="Minimum speech"
          type="number"
          min="64"
          max="2000"
          step="32"
          suffix="ms"
          hint="Loại speech burst quá ngắn; không nên giảm nếu môi trường có nhiều tiếng động."
          @update:model-value="setValue('asr.min_speech_duration_ms', $event)"
        />
        <UiSwitch
          :model-value="Boolean(draft['tts.speculative_prefetch_enabled'])"
          label="Speculative TTS prefetch"
          description="Tạo trước audio clause đầu từ speculative AI nhưng chỉ giữ trong RAM; không gửi audio/protocol trước khi ASR final khớp chính xác. Mismatch hoặc người dùng nói tiếp sẽ hủy buffer."
          @update:model-value="setValue('tts.speculative_prefetch_enabled', $event)"
        />
        <UiInput
          :model-value="draft['tts.stream_queue_max_chunks']"
          label="TTS engine buffer"
          type="number"
          min="1"
          max="256"
          step="1"
          suffix="chunks"
          hint="Buffer nội bộ giữa GPU TTS và playback. Tăng có thể nhả GPU lease sớm hơn cho phiên khác; AudioPacer vẫn giới hạn send-ahead xuống thiết bị."
          @update:model-value="setValue('tts.stream_queue_max_chunks', $event)"
        />
        <UiInput
          :model-value="draft['tts.first_audio_priority_boost']"
          label="First-audio priority boost"
          type="number"
          min="0"
          max="50"
          step="0.5"
          hint="Ưu tiên segment đầu của turn mới hơn continuation segment đã phát. Đây là scheduling latency, không đọc intent/nội dung."
          @update:model-value="setValue('tts.first_audio_priority_boost', $event)"
        />
        <UiInput
          :model-value="draft['tts.scheduler_aging_per_second']"
          label="TTS scheduler aging"
          type="number"
          min="0.1"
          max="20"
          step="0.1"
          suffix="pt/s"
          hint="Tăng ưu tiên theo thời gian chờ để continuation không bị starvation khi nhiều turn mới tới liên tục."
          @update:model-value="setValue('tts.scheduler_aging_per_second', $event)"
        />
        <UiInput
          :model-value="draft['tts.admission_timeout_ms']"
          label="TTS scheduler wait"
          type="number"
          min="100"
          max="30000"
          step="100"
          suffix="ms"
          hint="Budget chờ engine khi nhiều phiên cùng nói. Tách khỏi timeout tạo PCM đầu tiên nên không làm chậm fast path khi engine rảnh."
          @update:model-value="setValue('tts.admission_timeout_ms', $event)"
        />
        <UiInput
          :model-value="draft['tts.send_ahead_ms']"
          label="Voice send-ahead"
          type="number"
          min="60"
          max="1000"
          step="60"
          suffix="ms"
          hint="Buffer phát phía client. Thấp hơn giảm đuôi khi ngắt; quá thấp dễ hụt audio khi có jitter."
          @update:model-value="setValue('tts.send_ahead_ms', $event)"
        />
        <UiInput
          :model-value="draft['memory.lookup_timeout_ms']"
          label="Memory lookup budget"
          type="number"
          min="1"
          max="5000"
          step="1"
          suffix="ms"
          hint="Memory miss/timeout sẽ fail-open sang chat thường."
          @update:model-value="setValue('memory.lookup_timeout_ms', $event)"
        />
        <UiInput
          :model-value="draft['conversation.history_turns']"
          label="History turns"
          type="number"
          min="1"
          max="50"
          step="1"
          hint="Giữ đủ ngữ cảnh nhưng tránh prompt phình không cần thiết."
          @update:model-value="setValue('conversation.history_turns', $event)"
        />
        <UiInput
          :model-value="draft['tools.max_parallel_read_only']"
          label="Parallel read tools"
          type="number"
          min="1"
          max="4"
          step="1"
          hint="Giới hạn tool chỉ đọc chạy song song."
          @update:model-value="setValue('tools.max_parallel_read_only', $event)"
        />
        <UiInput
          :model-value="draft['tools.max_llm_rounds_per_turn']"
          label="Tool-chain rounds"
          type="number"
          min="2"
          max="4"
          step="1"
          hint="3 cho phép search → action → phản hồi. Chat thường vẫn chỉ 1 round."
          @update:model-value="setValue('tools.max_llm_rounds_per_turn', $event)"
        />
      </div>
    </section>

    <section class="section-card runtime-defaults-card">
      <header class="section-card__header">
        <div class="section-card__title-with-icon">
          <span><Gauge :size="18" /></span>
          <div>
            <h2>Speech streaming policy</h2>
            <p>Cắt stream LLM thành đơn vị TTS theo policy cấu hình. Đây là tuning prosody/latency, không phân loại intent hay match câu người dùng.</p>
          </div>
        </div>
        <div class="runtime-hot-note"><CheckCircle2 :size="15" />Hot apply</div>
      </header>

      <div class="runtime-defaults-grid">
        <UiInput
          :model-value="draft['llm.speech_segmentation.first_clause_min_chars']"
          label="First clause chars"
          type="number"
          min="4"
          max="500"
          step="1"
          hint="Giảm để TTS bắt đầu sớm hơn tại dấu phẩy; quá thấp có thể làm prosody cụt."
          @update:model-value="setValue('llm.speech_segmentation.first_clause_min_chars', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.first_clause_min_words']"
          label="First clause words"
          type="number"
          min="1"
          max="20"
          step="1"
          hint="Gate cấu trúc bổ sung cho đoạn đầu, độc lập ngôn ngữ/intent."
          @update:model-value="setValue('llm.speech_segmentation.first_clause_min_words', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.first_soft_cut_chars']"
          label="First soft-cut chars"
          type="number"
          min="0"
          max="200"
          step="1"
          hint="0 = tắt. Khi >0, đoạn nói đầu có thể cắt tại khoảng trắng sau ngưỡng này để TTS bắt đầu sớm hơn; không đọc keyword/intent."
          @update:model-value="setValue('llm.speech_segmentation.first_soft_cut_chars', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.first_soft_cut_min_words']"
          label="First soft-cut words"
          type="number"
          min="1"
          max="20"
          step="1"
          hint="Số từ tối thiểu trước soft-cut để tránh phát cụm quá vụn."
          @update:model-value="setValue('llm.speech_segmentation.first_soft_cut_min_words', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.first_segment_min_chars']"
          label="First sentence chars"
          type="number"
          min="2"
          max="200"
          step="1"
          @update:model-value="setValue('llm.speech_segmentation.first_segment_min_chars', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.first_segment_min_words']"
          label="First sentence words"
          type="number"
          min="1"
          max="20"
          step="1"
          @update:model-value="setValue('llm.speech_segmentation.first_segment_min_words', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.min_segment_chars']"
          label="Later sentence chars"
          type="number"
          min="4"
          max="500"
          step="1"
          @update:model-value="setValue('llm.speech_segmentation.min_segment_chars', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.clause_min_chars']"
          label="Clause search start"
          type="number"
          min="8"
          max="1000"
          step="1"
          @update:model-value="setValue('llm.speech_segmentation.clause_min_chars', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.clause_target_chars']"
          label="Clause target"
          type="number"
          min="16"
          max="1000"
          step="1"
          hint="Phải lớn hơn hoặc bằng Clause search start."
          @update:model-value="setValue('llm.speech_segmentation.clause_target_chars', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.hard_max_segment_chars']"
          label="Hard max segment"
          type="number"
          min="32"
          max="4000"
          step="1"
          hint="Safety bound cho text rất dài không có dấu câu."
          @update:model-value="setValue('llm.speech_segmentation.hard_max_segment_chars', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.hard_cut_search_back']"
          label="Hard-cut lookback"
          type="number"
          min="1"
          max="1000"
          step="1"
          @update:model-value="setValue('llm.speech_segmentation.hard_cut_search_back', $event)"
        />
        <UiInput
          :model-value="draft['llm.speech_segmentation.hard_cut_search_forward']"
          label="Hard-cut lookahead"
          type="number"
          min="1"
          max="500"
          step="1"
          @update:model-value="setValue('llm.speech_segmentation.hard_cut_search_forward', $event)"
        />
      </div>
    </section>
  </div>
</template>
