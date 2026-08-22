<script setup lang="ts">
import { computed, ref, useTemplateRef, watch } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import UiSelect, { type SelectOption } from '@/components/UiSelect.vue'
import { ApiError, listProviders, updateAgent } from '@/api/controlPlane'
import type { AgentSummary } from '@/types/agent'

const props = defineProps<{ open: boolean; agent: AgentSummary | null }>()
const emit = defineEmits<{ close: []; saved: [agent: AgentSummary]; reload: [] }>()

const rolePrompt = ref('')
const personality = ref('')
const addressStyle = ref('')
const language = ref('')
const detailLevel = ref('')
const responseStyle = ref('')
const modelId = ref('')

const catalogLoading = ref(false)
const catalogError = ref('')
const llmModels = ref<string[]>([])

const saving = ref(false)
const saveError = ref('')
const conflict = ref(false)
const rolePromptInput = useTemplateRef<HTMLTextAreaElement>('rolePromptInput')

// generation vô hiệu hóa phản hồi async cũ khi đóng/mở hộp thoại hoặc đổi trợ lý;
// catalogGeneration chỉ theo vòng đời yêu cầu tải provider catalog để retry catalog
// không làm mất kết quả của một lời lưu đang chạy.
let generation = 0
let catalogGeneration = 0

// Giá trị gợi ý cho các trường backend chấp nhận chuỗi tự do; giá trị hiện tại
// của trợ lý luôn được giữ làm lựa chọn đầu tiên nếu nằm ngoài danh sách.
const LANGUAGE_PRESETS: SelectOption[] = [
  { label: 'Tiếng Việt', value: 'vi-VN' },
  { label: 'English', value: 'en-US' },
  { label: '中文', value: 'zh-CN' },
  { label: '日本語', value: 'ja-JP' },
]

const DETAIL_LEVEL_PRESETS: SelectOption[] = [
  { label: 'Linh hoạt theo ngữ cảnh', value: 'adaptive' },
  { label: 'Ngắn gọn', value: 'concise' },
  { label: 'Chi tiết đầy đủ', value: 'detailed' },
]

function presetOptions(presets: SelectOption[], current: string): SelectOption[] {
  if (presets.some((option) => option.value === current)) return presets
  return [{ label: current || 'Mặc định hệ thống', value: current }, ...presets]
}

const languageOptions = computed(() => presetOptions(LANGUAGE_PRESETS, language.value))
const detailLevelOptions = computed(() => presetOptions(DETAIL_LEVEL_PRESETS, detailLevel.value))

const modelOptions = computed<SelectOption[]>(() => {
  const options = llmModels.value.map((model) => ({ label: model, value: model }))
  if (!llmModels.value.includes(modelId.value)) {
    options.unshift({ label: modelId.value || 'Mặc định hệ thống', value: modelId.value })
  }
  return options
})

async function loadCatalog() {
  const current = ++catalogGeneration
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const providers = await listProviders()
    // Chỉ nhận mô hình LLM từ provider catalog; kind khác không hiển thị.
    const models = [...new Set(providers.filter((provider) => provider.kind === 'llm').flatMap((provider) => provider.models))]
    if (current !== catalogGeneration || !props.open) return
    llmModels.value = models
  } catch (reason) {
    if (current !== catalogGeneration || !props.open) return
    catalogError.value = reason instanceof Error ? reason.message : 'Không tải được danh sách mô hình.'
  } finally {
    if (current === catalogGeneration && props.open) catalogLoading.value = false
  }
}

watch(
  () => [props.open, props.agent?.id] as const,
  ([open]) => {
    generation += 1
    if (!open) return
    catalogGeneration += 1
    const agent = props.agent
    rolePrompt.value = agent?.rolePrompt ?? ''
    personality.value = agent?.personality ?? ''
    addressStyle.value = agent?.addressStyle ?? ''
    language.value = agent?.language || 'vi-VN'
    detailLevel.value = agent?.detailLevel || 'adaptive'
    responseStyle.value = agent?.responseStyle ?? ''
    modelId.value = agent?.modelId ?? ''
    catalogLoading.value = false
    catalogError.value = ''
    llmModels.value = []
    saving.value = false
    saveError.value = ''
    conflict.value = false
    void loadCatalog()
    const openedGeneration = generation
    window.setTimeout(() => {
      if (props.open && generation === openedGeneration) rolePromptInput.value?.focus()
    })
  },
)

async function submit() {
  const target = props.agent
  if (!target || saving.value) return
  saving.value = true
  saveError.value = ''
  conflict.value = false
  const current = generation
  try {
    // Các trường chưa hiển thị (voice, memory, intent/tool) được giữ nguyên
    // nhờ spread target và mapping trong updateAgent.
    const updated = await updateAgent({
      ...target,
      rolePrompt: rolePrompt.value.trim(),
      personality: personality.value.trim(),
      addressStyle: addressStyle.value.trim(),
      language: language.value,
      detailLevel: detailLevel.value,
      responseStyle: responseStyle.value.trim(),
      modelId: modelId.value,
    })
    if (current !== generation) return
    emit('saved', updated)
    emit('close')
  } catch (reason) {
    if (current !== generation) return
    if ((reason as ApiError).status === 409) {
      conflict.value = true
      saveError.value = 'Trợ lý vừa được thay đổi ở nơi khác nên phiên bản bạn thấy đã cũ. Tải lại để lấy dữ liệu mới nhất.'
    } else {
      saveError.value = reason instanceof Error ? reason.message : 'Không lưu được cấu hình trợ lý.'
    }
  } finally {
    if (current === generation) saving.value = false
  }
}

function reloadAfterConflict() {
  emit('reload')
  emit('close')
}

function requestClose() {
  if (!saving.value) emit('close')
}
</script>

<template>
  <UiDialog
    :open="open"
    title="Cấu hình trợ lý"
    description="Thay đổi có hiệu lực từ lượt hội thoại tiếp theo; lượt hội thoại đang chạy giữ nguyên cấu hình cũ."
    variant="compact"
    @close="requestClose"
  >
    <form class="config-form" @submit.prevent="submit">
      <label class="config-field">
        <span>Vai trò tổng quát</span>
        <textarea ref="rolePromptInput" v-model="rolePrompt" class="config-textarea" rows="3" maxlength="12000" data-dialog-autofocus data-testid="config-role-prompt"></textarea>
      </label>
      <label class="config-field">
        <span>Tính cách</span>
        <textarea v-model="personality" class="config-textarea" rows="2" maxlength="4000" data-testid="config-personality"></textarea>
      </label>
      <div class="form-grid">
        <label class="config-field">
          <span>Cách xưng hô</span>
          <input v-model="addressStyle" class="text-input" maxlength="2000" autocomplete="off" data-testid="config-address-style" />
        </label>
        <label class="config-field">
          <span>Phong cách trả lời</span>
          <input v-model="responseStyle" class="text-input" maxlength="2000" autocomplete="off" data-testid="config-response-style" />
        </label>
      </div>
      <div class="form-grid">
        <div class="config-field">
          <span>Ngôn ngữ</span>
          <UiSelect v-model="language" label="Ngôn ngữ" :options="languageOptions" />
        </div>
        <div class="config-field">
          <span>Mức độ chi tiết</span>
          <UiSelect v-model="detailLevel" label="Mức độ chi tiết" :options="detailLevelOptions" />
        </div>
      </div>
      <div class="config-field">
        <span>Mô hình ngôn ngữ</span>
        <p v-if="catalogLoading" class="field-help" role="status" data-testid="config-catalog-loading">Đang tải danh sách mô hình...</p>
        <div v-else-if="catalogError" class="catalog-state">
          <p class="form-message error-message" role="alert" data-testid="config-catalog-error">Không tải được danh sách mô hình. {{ catalogError }}</p>
          <button class="button button-outline" type="button" data-testid="config-catalog-retry" @click="loadCatalog">Thử lại</button>
        </div>
        <UiSelect v-else v-model="modelId" label="Mô hình ngôn ngữ" :options="modelOptions" />
        <small>Danh sách mô hình lấy từ provider catalog máy chủ.</small>
      </div>
      <p v-if="saveError" class="form-message error-message" role="alert" data-testid="config-error">{{ saveError }}</p>
      <button class="visually-hidden" type="submit" tabindex="-1" aria-hidden="true">Lưu</button>
    </form>
    <template #footer>
      <button class="button button-outline" type="button" :disabled="saving" data-testid="config-cancel" @click="requestClose">{{ conflict ? 'Đóng' : 'Hủy' }}</button>
      <button v-if="conflict" class="button button-outline" type="button" data-testid="config-reload" @click="reloadAfterConflict">Tải lại</button>
      <button class="button button-primary" type="button" :disabled="saving" data-testid="config-save" @click="submit">{{ saving ? 'Đang lưu...' : 'Lưu cấu hình' }}</button>
    </template>
  </UiDialog>
</template>
