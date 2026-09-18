<script setup>
import { KeyRound, RotateCcw, Save, ServerCog, ShieldCheck } from '@lucide/vue'
import PageHeader from '../components/layout/PageHeader.vue'
import UiBadge from '../components/ui/UiBadge.vue'
import UiButton from '../components/ui/UiButton.vue'
import UiInput from '../components/ui/UiInput.vue'
import UiSegmented from '../components/ui/UiSegmented.vue'
import UiSelect from '../components/ui/UiSelect.vue'

const props = defineProps({
  draft: { type: Object, required: true },
  runtime: { type: Object, default: () => ({}) },
  models: { type: Array, default: () => [] },
  voices: { type: Array, default: () => [] },
  busy: Boolean,
  restartRequired: Boolean,
  secretPlaceholder: { type: Function, required: true },
})
const emit = defineEmits(['update', 'save'])
const secretKeys = ['GROQ_API_KEY_A', 'GROQ_API_KEY_B', 'GROQ_API_KEY_C', 'GROQ_API_KEY_D', 'DEEPGRAM_API_KEY', 'HF_TOKEN']
const modelOptions = () => props.models.map(item => ({ value: item, label: item }))
const voiceOptions = () => props.voices.map(item => ({ value: item.name, label: item.name, description: item.description || '' }))
function setValue(key, value) { emit('update', key, value) }
</script>

<template>
  <div class="workspace-view runtime-view">
    <PageHeader eyebrow="05 / System" title="Runtime & Secrets" description="Cấu hình provider, global voice defaults và credential mà không để lộ secret thô ra browser.">
      <template #actions>
        <UiButton variant="primary" :loading="busy" @click="emit('save')"><template #icon><Save :size="15" /></template>Lưu thay đổi</UiButton>
      </template>
    </PageHeader>

    <div v-if="restartRequired" class="restart-banner">
      <span><RotateCcw :size="17" /></span>
      <div><strong>Cần restart server</strong><p>Một số provider credential hoặc cấu hình nền đã thay đổi.</p></div>
      <UiBadge tone="warning">Pending restart</UiBadge>
    </div>

    <div class="settings-workspace">
      <section class="settings-section">
        <aside class="settings-section__intro">
          <span>01 / CREDENTIALS</span>
          <div class="settings-section__icon"><ShieldCheck :size="19" /></div>
          <h2>Provider secrets</h2>
          <p>Để trống nếu muốn giữ nguyên giá trị hiện tại. Secret mới chỉ được gửi lên server một lần.</p>
        </aside>

        <div class="settings-section__panel">
          <div class="settings-section__panel-head">
            <div><strong>API credentials</strong><span>Masked after save</span></div>
            <KeyRound :size="16" />
          </div>
          <div class="secret-grid settings-secret-grid">
            <UiInput
              v-for="key in secretKeys"
              :key="key"
              :model-value="draft[key]"
              :label="key"
              type="password"
              autocomplete="new-password"
              :placeholder="secretPlaceholder(key)"
              class="mono-field"
              @update:model-value="setValue(key, $event)"
            />
          </div>
        </div>
      </section>

      <section class="settings-section">
        <aside class="settings-section__intro">
          <span>02 / DEFAULTS</span>
          <div class="settings-section__icon"><ServerCog :size="19" /></div>
          <h2>Voice runtime</h2>
          <p>Global defaults cho model và voice. Assistant-specific settings vẫn được ưu tiên khi kết nối.</p>
        </aside>

        <div class="settings-section__panel">
          <div class="settings-section__panel-head">
            <div><strong>Runtime defaults</strong><span>Global fallback configuration</span></div>
            <ServerCog :size="16" />
          </div>

          <div class="runtime-form-grid">
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

            <div class="runtime-choice">
              <div><span>ASR DEVICE</span><strong>Thiết bị chạy model nhận dạng giọng nói</strong></div>
              <UiSegmented
                :model-value="draft['asr.device']"
                :options="[{ value: 'cuda', label: 'CUDA' }, { value: 'cpu', label: 'CPU' }]"
                @update:model-value="setValue('asr.device', $event)"
              />
            </div>

            <div class="runtime-choice">
              <div><span>BARGE-IN POLICY</span><strong>Cách server xử lý khi người dùng nói chen</strong></div>
              <UiSegmented
                :model-value="draft['server.barge_in_policy']"
                :options="[{ value: 'client_only', label: 'Client only' }, { value: 'automatic', label: 'Automatic' }]"
                @update:model-value="setValue('server.barge_in_policy', $event)"
              />
            </div>
          </div>
        </div>
      </section>
    </div>

    <footer class="security-footer">
      <div><KeyRound :size="16" /></div>
      <div><strong>Secret-safe management</strong><span>Giá trị đã lưu chỉ hiển thị masked; phiên quản trị dùng HttpOnly cookie.</span></div>
    </footer>
  </div>
</template>