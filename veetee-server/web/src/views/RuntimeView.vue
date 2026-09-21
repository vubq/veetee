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
    <PageHeader
      eyebrow="Runtime"
      title="Runtime & Secrets"
      description="Quản lý provider credential và global defaults. Secret đã lưu chỉ được trả về dưới dạng masked."
    >
      <template #actions>
        <UiButton variant="primary" :loading="busy" @click="emit('save')">
          <template #icon><Save :size="16" /></template>
          Lưu thay đổi
        </UiButton>
      </template>
    </PageHeader>

    <div v-if="restartRequired" class="restart-alert">
      <span><RotateCcw :size="18" /></span>
      <div>
        <strong>Cần restart server</strong>
        <p>Một số credential hoặc cấu hình nền vừa thay đổi và cần restart để áp dụng đầy đủ.</p>
      </div>
      <UiBadge tone="warning">Pending restart</UiBadge>
    </div>

    <div class="runtime-layout">
      <section class="section-card credentials-card">
        <header class="section-card__header">
          <div class="section-card__title-with-icon">
            <span><ShieldCheck :size="18" /></span>
            <div>
              <h2>API credentials</h2>
              <p>Để trống để giữ nguyên secret hiện tại.</p>
            </div>
          </div>
          <KeyRound :size="18" />
        </header>

        <div class="credentials-grid">
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

        <footer class="section-card__footer-note">
          <KeyRound :size="15" />
          <span>Secret không được đưa vào localStorage và không hiển thị lại dưới dạng plaintext.</span>
        </footer>
      </section>

      <section class="section-card runtime-defaults-card">
        <header class="section-card__header">
          <div class="section-card__title-with-icon">
            <span><ServerCog :size="18" /></span>
            <div>
              <h2>Runtime defaults</h2>
              <p>Global fallback khi Assistant không có override riêng.</p>
            </div>
          </div>
        </header>

        <div class="runtime-defaults-form">
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
              <span>Thiết bị chạy model nhận dạng giọng nói.</span>
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
              <span>Cách server xử lý khi người dùng nói chen.</span>
            </div>
            <UiSegmented
              :model-value="draft['server.barge_in_policy']"
              :options="[{ value: 'client_only', label: 'Client only' }, { value: 'automatic', label: 'Automatic' }]"
              @update:model-value="setValue('server.barge_in_policy', $event)"
            />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
