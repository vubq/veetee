<script setup lang="ts">
import {
  Activity,
  BookOpen,
  Bot,
  Box,
  ClipboardList,
  LogOut,
  PieChart,
  Plug,
  Server,
  Shield,
  Sliders,
  UserRound,
  Users,
} from '@lucide/vue'
import { computed, ref } from 'vue'

import RobotLogo from '@/components/RobotLogo.vue'
import UiDropdown, { type DropdownItem } from '@/components/UiDropdown.vue'
import { authState, logout } from '@/api/controlPlane'

export type ConsoleTab =
  | 'agents'
  | 'providers'
  | 'knowledge'
  | 'corrections'
  | 'tools'
  | 'ota'
  | 'users'
  | 'settings-quota'
  | 'audit'

const props = defineProps<{
  activeTab: ConsoleTab
}>()

const emit = defineEmits<{
  selectTab: [tab: ConsoleTab]
}>()

const loggingOut = ref(false)

const activeGroup = computed<'agents' | 'operations' | 'admin'>(() => {
  if (props.activeTab === 'agents') return 'agents'
  if (['providers', 'knowledge', 'corrections', 'tools', 'ota'].includes(props.activeTab)) return 'operations'
  if (['users', 'settings-quota', 'audit'].includes(props.activeTab)) return 'admin'
  return 'agents'
})

const lastOperationsSubtab = ref<ConsoleTab>('providers')
const lastAdminSubtab = ref<ConsoleTab>('users')

function handleGroupClick(group: 'agents' | 'operations' | 'admin') {
  if (group === 'agents') {
    emit('selectTab', 'agents')
  } else if (group === 'operations') {
    emit('selectTab', ['providers', 'knowledge', 'corrections', 'tools', 'ota'].includes(props.activeTab) ? props.activeTab : lastOperationsSubtab.value)
  } else if (group === 'admin') {
    emit('selectTab', ['users', 'settings-quota', 'audit'].includes(props.activeTab) ? props.activeTab : lastAdminSubtab.value)
  }
}

function handleSubtabClick(tab: ConsoleTab) {
  if (['providers', 'knowledge', 'corrections', 'tools', 'ota'].includes(tab)) {
    lastOperationsSubtab.value = tab
  } else if (['users', 'settings-quota', 'audit'].includes(tab)) {
    lastAdminSubtab.value = tab
  }
  emit('selectTab', tab)
}

const accountItems: DropdownItem[] = [
  { label: 'Đăng xuất', value: 'logout', icon: LogOut },
]

async function handleAccountSelect(value: string) {
  if (value !== 'logout' || loggingOut.value) return
  loggingOut.value = true
  try {
    await logout()
  } finally {
    loggingOut.value = false
  }
}
</script>

<template>
  <header class="app-header" :class="{ 'has-subnav': activeGroup !== 'agents' }">
    <div class="page-container header-inner">
      <button class="brand" type="button" aria-label="Bảng điều khiển Veetee" @click="emit('selectTab', 'agents')">
        <RobotLogo :size="34" />
        <span>Bảng điều khiển Veetee</span>
      </button>

      <nav class="primary-nav" aria-label="Điều hướng chính">
        <button
          class="nav-item"
          :class="{ 'is-active': activeGroup === 'agents' }"
          :aria-current="activeGroup === 'agents' ? 'page' : undefined"
          type="button"
          title="Trợ lý"
          @click="handleGroupClick('agents')"
        >
          <Bot :size="17" stroke-width="1.8" />
          <span class="nav-label">Trợ lý</span>
        </button>

        <button
          class="nav-item"
          :class="{ 'is-active': activeGroup === 'operations' }"
          :aria-current="activeGroup === 'operations' ? 'page' : undefined"
          type="button"
          title="Vận hành"
          @click="handleGroupClick('operations')"
        >
          <Activity :size="17" stroke-width="1.8" />
          <span class="nav-label">Vận hành</span>
        </button>

        <button
          class="nav-item"
          :class="{ 'is-active': activeGroup === 'admin' }"
          :aria-current="activeGroup === 'admin' ? 'page' : undefined"
          type="button"
          title="Quản trị"
          @click="handleGroupClick('admin')"
        >
          <Shield :size="17" stroke-width="1.8" />
          <span class="nav-label">Quản trị</span>
        </button>
      </nav>

      <div class="header-actions">
        <UiDropdown label="Menu tài khoản" :items="accountItems" @select="handleAccountSelect">
          <template #trigger>
            <button class="account-button" type="button" aria-label="Menu tài khoản">
              <UserRound :size="16" />
              <span v-if="authState.userEmail" class="account-email">{{ authState.userEmail }}</span>
            </button>
          </template>
        </UiDropdown>
      </div>
    </div>

    <!-- Secondary Subnav bar for Operations and Admin -->
    <div v-if="activeGroup !== 'agents'" class="subnav-bar">
      <div class="page-container subnav-inner">
        <nav v-if="activeGroup === 'operations'" class="sub-nav" aria-label="Điều hướng Vận hành">
          <button
            class="subnav-item"
            :class="{ 'is-active': activeTab === 'providers' }"
            type="button"
            @click="handleSubtabClick('providers')"
          >
            <Server :size="15" />
            <span>Nhà cung cấp</span>
          </button>

          <button
            class="subnav-item"
            :class="{ 'is-active': activeTab === 'knowledge' }"
            type="button"
            @click="handleSubtabClick('knowledge')"
          >
            <BookOpen :size="15" />
            <span>Kho kiến thức</span>
          </button>

          <button
            class="subnav-item"
            :class="{ 'is-active': activeTab === 'corrections' }"
            type="button"
            @click="handleSubtabClick('corrections')"
          >
            <Sliders :size="15" />
            <span>Hiệu chỉnh & ngữ cảnh</span>
          </button>

          <button
            class="subnav-item"
            :class="{ 'is-active': activeTab === 'tools' }"
            type="button"
            @click="handleSubtabClick('tools')"
          >
            <Plug :size="15" />
            <span>Tích hợp & thiết bị</span>
          </button>

          <button
            class="subnav-item"
            :class="{ 'is-active': activeTab === 'ota' }"
            type="button"
            @click="handleSubtabClick('ota')"
          >
            <Box :size="15" />
            <span>Firmware OTA</span>
          </button>
        </nav>

        <nav v-else-if="activeGroup === 'admin'" class="sub-nav" aria-label="Điều hướng Quản trị">
          <button
            class="subnav-item"
            :class="{ 'is-active': activeTab === 'users' }"
            type="button"
            @click="handleSubtabClick('users')"
          >
            <Users :size="15" />
            <span>User</span>
          </button>

          <button
            class="subnav-item"
            :class="{ 'is-active': activeTab === 'settings-quota' }"
            type="button"
            @click="handleSubtabClick('settings-quota')"
          >
            <PieChart :size="15" />
            <span>Cài đặt & quota</span>
          </button>

          <button
            class="subnav-item"
            :class="{ 'is-active': activeTab === 'audit' }"
            type="button"
            @click="handleSubtabClick('audit')"
          >
            <ClipboardList :size="15" />
            <span>Audit</span>
          </button>
        </nav>
      </div>
    </div>
  </header>
</template>
