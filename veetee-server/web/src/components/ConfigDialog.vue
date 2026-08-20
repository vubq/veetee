<script setup lang="ts">
import {
  AlignLeft,
  BookOpen,
  Bot,
  ChevronDown,
  CircleSlash,
  Gauge,
  Languages,
  ListTree,
  MessageSquareText,
  Plus,
  Rabbit,
  Scale,
  Snail,
  Sparkles,
  Trash2,
  UserRound,
  Volume2,
  WandSparkles,
} from '@lucide/vue'
import { ref } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import UiSelect from '@/components/UiSelect.vue'
import UiSwitch from '@/components/UiSwitch.vue'

defineProps<{ open: boolean; agentName: string }>()
const emit = defineEmits<{ close: [] }>()

type TabValue = 'role' | 'model' | 'speaker' | 'extension'

const activeTab = ref<TabValue>('role')
const language = ref('vi')
const voice = ref('female-warm')
const customRole = ref(false)
const rolePrompt = ref(`# Vai trò: Người bạn đồng hành ấm áp và chân thành

## Đặc điểm nhân vật
Giọng nói trẻ trung, truyền cảm và gần gũi. Luôn lắng nghe, phản hồi tinh tế và mang lại năng lượng tích cực.

## Phương thức tương tác
Trò chuyện tự nhiên, chủ động gợi mở bằng những câu hỏi quan tâm. Đồng cảm với cảm xúc của người dùng và không phán xét.

## Phong cách ngôn ngữ
Sử dụng tiếng Việt thuần Việt, giàu cảm xúc, tránh cách diễn đạt quá trang trọng hoặc máy móc.`)
const voiceSettingsOpen = ref(false)
const speakingRate = ref('normal')
const responseStyle = ref('balanced')

const model = ref('veetee-lite')
const minorMode = ref(false)
const memoryEnabled = ref(true)
const memoryOpen = ref(false)
const memoryItems = ref([
  { id: 1, content: 'Người dùng ưu tiên giao tiếp bằng tiếng Việt.' },
  { id: 2, content: 'Trả lời ngắn gọn khi người dùng đang điều khiển thiết bị.' },
])

const speakers = ref<Array<{ id: number; name: string; description: string }>>([])
const addingSpeaker = ref(false)
const speakerName = ref('')
const speakerDescription = ref('')

const weatherEnabled = ref(true)
const musicEnabled = ref(false)
const knowledgeEnabled = ref(false)
const knowledgeBase = ref('none')
const mcpOpen = ref(false)
const mcpEnabled = ref(false)
const mcpEndpoint = ref('')
const saved = ref(false)

const tabs: Array<{ value: TabValue; label: string }> = [
  { value: 'role', label: 'Vai trò' },
  { value: 'model', label: 'Mô hình & bộ nhớ' },
  { value: 'speaker', label: 'Nhận dạng người nói' },
  { value: 'extension', label: 'Mở rộng' },
]

const languages = [
  { value: 'vi', label: 'Tiếng Việt', icon: Languages },
  { value: 'en', label: 'Tiếng Anh', icon: Languages },
  { value: 'ja', label: 'Tiếng Nhật', icon: Languages },
  { value: 'zh', label: 'Tiếng Trung', icon: Languages },
]
const voices = [
  { value: 'female-warm', label: 'Giọng nữ · Ấm áp', icon: Volume2 },
  { value: 'female-clear', label: 'Giọng nữ · Trong trẻo', icon: Sparkles },
  { value: 'male-calm', label: 'Giọng nam · Điềm tĩnh', icon: UserRound },
]
const models = [
  { value: 'veetee-lite', label: 'Veetee Lite · Nhanh', icon: Bot },
  { value: 'veetee-balanced', label: 'Veetee Balanced · Cân bằng', icon: Scale },
  { value: 'veetee-pro', label: 'Veetee Pro · Chuyên sâu', icon: WandSparkles },
]
const speakingRates = [
  { value: 'slow', label: 'Chậm', icon: Snail },
  { value: 'normal', label: 'Bình thường', icon: Gauge },
  { value: 'fast', label: 'Nhanh', icon: Rabbit },
]
const responseStyles = [
  { value: 'concise', label: 'Ngắn gọn', icon: AlignLeft },
  { value: 'balanced', label: 'Cân bằng', icon: MessageSquareText },
  { value: 'detailed', label: 'Chi tiết', icon: ListTree },
]
const knowledgeBases = [
  { value: 'none', label: 'Không dùng kho kiến thức', icon: CircleSlash },
  { value: 'home-guide', label: 'Hướng dẫn sử dụng gia đình', icon: BookOpen },
  { value: 'product-help', label: 'Trợ giúp sản phẩm Veetee', icon: Sparkles },
]

function selectTab(tab: TabValue) {
  activeTab.value = tab
  saved.value = false
}

function addSpeaker() {
  const name = speakerName.value.trim()
  if (!name) return
  speakers.value.push({
    id: Date.now(),
    name,
    description: speakerDescription.value.trim() || 'Không có mô tả',
  })
  speakerName.value = ''
  speakerDescription.value = ''
  addingSpeaker.value = false
}

function save() {
  saved.value = true
}
</script>

<template>
  <UiDialog
    :open="open"
    :title="`${agentName} · Cấu hình`"
    description="Cấu hình vai trò, mô hình, nhận dạng người nói và khả năng mở rộng."
    size="large"
    @close="emit('close')"
  >
    <form class="config-form" @submit.prevent="save">
      <div class="tabs" role="tablist" aria-label="Nhóm cấu hình">
        <button
          v-for="tab in tabs"
          :id="`config-tab-${tab.value}`"
          :key="tab.value"
          type="button"
          role="tab"
          :aria-controls="`config-panel-${tab.value}`"
          :aria-selected="activeTab === tab.value"
          :tabindex="activeTab === tab.value ? 0 : -1"
          :class="{ active: activeTab === tab.value }"
          @click="selectTab(tab.value)"
        >{{ tab.label }}</button>
      </div>

      <section
        v-if="activeTab === 'role'"
        id="config-panel-role"
        class="form-section"
        role="tabpanel"
        aria-labelledby="config-tab-role"
      >
        <div class="form-grid">
          <label><span>Ngôn ngữ đối thoại</span><UiSelect v-model="language" label="Ngôn ngữ đối thoại" :options="languages" /></label>
          <label><span>Vai trò giọng nói</span><UiSelect v-model="voice" label="Vai trò giọng nói" :options="voices" /></label>
        </div>

        <div class="section-heading">
          <div><strong>Giới thiệu vai trò</strong><p>Hướng dẫn cách trợ lý giao tiếp và phản hồi</p></div>
          <label class="switch-label">Tùy chỉnh <UiSwitch v-model="customRole" label="Tùy chỉnh giới thiệu vai trò" /></label>
        </div>
        <textarea v-if="customRole" v-model="rolePrompt" class="config-textarea role-editor" aria-label="Giới thiệu vai trò tùy chỉnh" />
        <div v-else class="role-preview">
          <strong># Vai trò: Người bạn đồng hành ấm áp và chân thành</strong>
          <p>Giọng nói trẻ trung, truyền cảm và gần gũi. Luôn lắng nghe, phản hồi tinh tế và mang lại năng lượng tích cực.</p>
          <p>Trò chuyện tự nhiên bằng tiếng Việt, chủ động hỗ trợ nhưng không phán xét hay diễn đạt máy móc.</p>
        </div>

        <button class="accordion-button" type="button" :aria-expanded="voiceSettingsOpen" @click="voiceSettingsOpen = !voiceSettingsOpen">
          <span><strong>Cài đặt giọng nói</strong><small>Tốc độ và độ dài phản hồi</small></span>
          <ChevronDown :size="16" :class="{ rotated: voiceSettingsOpen }" />
        </button>
        <div v-if="voiceSettingsOpen" class="accordion-panel form-grid">
          <label><span>Tốc độ nói</span><UiSelect v-model="speakingRate" label="Tốc độ nói" :options="speakingRates" /></label>
          <label><span>Phong cách phản hồi</span><UiSelect v-model="responseStyle" label="Phong cách phản hồi" :options="responseStyles" /></label>
        </div>
      </section>

      <section
        v-else-if="activeTab === 'model'"
        id="config-panel-model"
        class="form-section"
        role="tabpanel"
        aria-labelledby="config-tab-model"
      >
        <label class="config-field"><span class="field-label">Mô hình ngôn ngữ</span><UiSelect v-model="model" label="Mô hình ngôn ngữ" :options="models" /></label>
        <p class="field-help">Tên mô hình là dữ liệu giao diện mẫu; provider backend chưa được kết nối.</p>

        <div class="setting-list">
          <div class="setting-row">
            <div><strong>Chế độ vị thành niên</strong><p>Lọc nội dung nhạy cảm và giới hạn chủ đề không phù hợp.</p></div>
            <UiSwitch v-model="minorMode" label="Chế độ vị thành niên" />
          </div>
          <div class="setting-row">
            <div><strong>Bộ nhớ dài hạn</strong><p>Ghi nhớ sở thích đã xác nhận để cá nhân hóa hội thoại.</p></div>
            <UiSwitch v-model="memoryEnabled" label="Bộ nhớ dài hạn" />
          </div>
        </div>

        <button class="accordion-button" type="button" :disabled="!memoryEnabled" :aria-expanded="memoryOpen" @click="memoryOpen = !memoryOpen">
          <span><strong>Mục bộ nhớ</strong><small>{{ memoryItems.length }} mục đang lưu</small></span>
          <ChevronDown :size="16" :class="{ rotated: memoryOpen }" />
        </button>
        <div v-if="memoryOpen && memoryEnabled" class="accordion-panel memory-list">
          <div v-for="item in memoryItems" :key="item.id" class="memory-item">
            <span>{{ item.content }}</span>
            <button type="button" :aria-label="`Xóa: ${item.content}`" @click="memoryItems = memoryItems.filter((entry) => entry.id !== item.id)"><Trash2 :size="15" /></button>
          </div>
          <p v-if="memoryItems.length === 0" class="inline-empty">Chưa có mục bộ nhớ.</p>
        </div>
      </section>

      <section
        v-else-if="activeTab === 'speaker'"
        id="config-panel-speaker"
        class="form-section"
        role="tabpanel"
        aria-labelledby="config-tab-speaker"
      >
        <div class="panel-toolbar">
          <div><strong>Hồ sơ người nói</strong><p>Đặt tên cho giọng nói quen thuộc sau khi đã có mẫu nhận dạng.</p></div>
          <button class="button button-secondary" type="button" @click="addingSpeaker = !addingSpeaker"><Plus :size="15" /> Thêm người nói</button>
        </div>

        <div v-if="addingSpeaker" class="inline-form">
          <label><span class="field-label">Tên người nói</span><input v-model="speakerName" class="text-input" placeholder="Ví dụ: Bố" /></label>
          <label><span class="field-label">Mô tả</span><input v-model="speakerDescription" class="text-input" placeholder="Giọng trầm, nói chậm" /></label>
          <div class="inline-form-actions">
            <button class="button button-ghost" type="button" @click="addingSpeaker = false">Hủy</button>
            <button class="button button-primary" type="button" :disabled="!speakerName.trim()" @click="addSpeaker">Thêm</button>
          </div>
        </div>

        <div class="speaker-table">
          <div class="speaker-table-head"><span>Tên</span><span>Mô tả</span><span>Thao tác</span></div>
          <div v-for="speaker in speakers" :key="speaker.id" class="speaker-table-row">
            <strong>{{ speaker.name }}</strong><span>{{ speaker.description }}</span>
            <button class="danger-action" type="button" @click="speakers = speakers.filter((item) => item.id !== speaker.id)"><Trash2 :size="14" /> Xóa</button>
          </div>
          <div v-if="speakers.length === 0" class="config-empty"><strong>Chưa có người nói được nhận dạng</strong><p>Thêm hồ sơ để gán tên cho giọng nói quen thuộc.</p></div>
        </div>
      </section>

      <section
        v-else
        id="config-panel-extension"
        class="form-section"
        role="tabpanel"
        aria-labelledby="config-tab-extension"
      >
        <div class="section-title"><strong>Dịch vụ tích hợp</strong><p>Bật những khả năng trợ lý được phép sử dụng.</p></div>
        <div class="service-grid">
          <label class="service-card" :class="{ enabled: weatherEnabled }"><span><strong>Thời tiết</strong><small>Dự báo theo vị trí</small></span><UiSwitch v-model="weatherEnabled" label="Dịch vụ thời tiết" /></label>
          <label class="service-card" :class="{ enabled: musicEnabled }"><span><strong>Âm nhạc</strong><small>Tìm và phát nội dung</small></span><UiSwitch v-model="musicEnabled" label="Dịch vụ âm nhạc" /></label>
          <label class="service-card" :class="{ enabled: knowledgeEnabled }"><span><strong>Cơ sở tri thức</strong><small>Tra cứu tài liệu riêng</small></span><UiSwitch v-model="knowledgeEnabled" label="Dịch vụ cơ sở tri thức" /></label>
        </div>

        <label class="config-field" :class="{ disabled: !knowledgeEnabled }">
          <span class="field-label">Cơ sở tri thức</span>
          <UiSelect v-model="knowledgeBase" label="Cơ sở tri thức" :options="knowledgeBases" />
          <small>Chỉ được sử dụng khi dịch vụ cơ sở tri thức đang bật.</small>
        </label>

        <div class="section-title extension-custom"><strong>Dịch vụ tùy chỉnh</strong><p>Kết nối công cụ qua một điểm cuối MCP.</p></div>
        <button class="accordion-button" type="button" :aria-expanded="mcpOpen" @click="mcpOpen = !mcpOpen">
          <span><strong>Điểm cuối MCP</strong><small>{{ mcpEnabled ? 'Đang bật' : 'Chưa bật' }}</small></span>
          <ChevronDown :size="16" :class="{ rotated: mcpOpen }" />
        </button>
        <div v-if="mcpOpen" class="accordion-panel">
          <div class="setting-row compact-setting">
            <div><strong>Cho phép công cụ MCP</strong><p>Trợ lý chỉ gọi công cụ sau khi điểm cuối được xác thực.</p></div>
            <UiSwitch v-model="mcpEnabled" label="Cho phép công cụ MCP" />
          </div>
          <label class="config-field"><span class="field-label">URL điểm cuối</span><input v-model="mcpEndpoint" class="text-input" type="url" placeholder="https://mcp.example.com/events" :disabled="!mcpEnabled" /></label>
          <p class="field-help">Không nhập token hoặc secret vào trường URL.</p>
        </div>
      </section>

      <p v-if="saved" class="save-status" role="status">Đã lưu cấu hình giao diện mẫu.</p>
    </form>

    <template #footer>
      <button class="button button-ghost" type="button" @click="emit('close')">Hủy</button>
      <button class="button button-primary" type="button" @click="save">Lưu</button>
    </template>
  </UiDialog>
</template>
