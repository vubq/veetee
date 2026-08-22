<script setup lang="ts">
import {
  Bot,
  RadioTower,
  ChevronDown,
  Languages,
  LibraryBig,
  LogOut,
  MicVocal,
  Monitor,
  Moon,
  Settings,
  Sun,
  UserRound,
} from '@lucide/vue'

import IconButton from '@/components/IconButton.vue'
import RobotLogo from '@/components/RobotLogo.vue'
import UiDropdown, { type DropdownItem } from '@/components/UiDropdown.vue'

type View = 'agents' | 'ota'
const props = defineProps<{ activeView: View }>()
const emit = defineEmits<{ navigate: [view: View]; logout: [] }>()
const navItems = [
  { label: 'Trợ lý', icon: Bot, view: 'agents' as const },
  { label: 'Nhân bản giọng nói', icon: MicVocal, disabled: true, suffix: 'Sắp có' },
  { label: 'Kho kiến thức', icon: LibraryBig, disabled: true, suffix: 'Sắp có' },
  { label: 'OTA', icon: RadioTower, view: 'ota' as const },
]

const languageItems: DropdownItem[] = [
  { label: '简体中文', value: 'zh-cn', icon: Languages },
  { label: '繁體中文', value: 'zh-tw', icon: Languages },
  { label: 'English', value: 'en', icon: Languages },
  { label: '日本語', value: 'ja', icon: Languages },
  { label: 'Tiếng Việt', value: 'vi', checked: true, icon: Languages },
]
const themeItems: DropdownItem[] = [
  { label: 'Sáng', value: 'light', checked: true, icon: Sun },
  { label: 'Tối', value: 'dark', icon: Moon },
  { label: 'Theo hệ thống', value: 'system', icon: Monitor },
]
const accountItems: DropdownItem[] = [
  { label: 'Hồ sơ cá nhân · Sắp có', value: 'profile', icon: UserRound, disabled: true },
  { label: 'Cài đặt · Sắp có', value: 'settings', icon: Settings, disabled: true },
  { label: 'Đăng xuất', value: 'logout', icon: LogOut },
]

function selectAccount(value: string) {
  if (value === 'logout') emit('logout')
}
</script>

<template>
  <header class="app-header">
    <div class="page-container header-inner">
      <button class="brand" type="button" aria-label="Bảng điều khiển Veetee" @click="emit('navigate', 'agents')">
        <RobotLogo :size="34" />
        <span>Bảng điều khiển Veetee</span>
      </button>

      <nav class="primary-nav" aria-label="Điều hướng chính">
        <button
          v-for="item in navItems"
          :key="item.label"
          class="nav-item"
          :class="{ 'is-active': item.view === props.activeView }"
          :aria-current="item.view === props.activeView ? 'page' : undefined"
          :disabled="item.disabled"
          type="button"
          :title="item.label"
          @click="item.view && emit('navigate', item.view)"
        >
          <component :is="item.icon" :size="17" stroke-width="1.8" />
          <span class="nav-label">{{ item.label }}</span>
          <span v-if="item.suffix" class="nav-badge">{{ item.suffix }}</span>
        </button>
      </nav>

      <div class="header-actions">
        <UiDropdown label="Chọn ngôn ngữ: VI" :items="languageItems">
          <template #trigger><IconButton label="Chọn ngôn ngữ: VI"><Languages :size="17" /></IconButton></template>
        </UiDropdown>
        <UiDropdown label="Chọn giao diện: Sáng" :items="themeItems">
          <template #trigger><IconButton label="Chọn giao diện: Sáng"><Moon :size="17" /></IconButton></template>
        </UiDropdown>
        <UiDropdown label="Menu tài khoản" :items="accountItems" @select="selectAccount">
          <template #trigger><button class="account-button" type="button" aria-label="Menu tài khoản"><span>Q</span><ChevronDown :size="14" /></button></template>
        </UiDropdown>
      </div>
    </div>
  </header>
</template>
