<script setup lang="ts">
import { Bot, Box, LogOut, UserRound } from '@lucide/vue'
import { ref } from 'vue'

import RobotLogo from '@/components/RobotLogo.vue'
import UiDropdown, { type DropdownItem } from '@/components/UiDropdown.vue'
import { authState, logout } from '@/api/controlPlane'

defineProps<{
  activeTab: 'agents' | 'ota'
}>()

const emit = defineEmits<{
  selectTab: [tab: 'agents' | 'ota']
}>()

const loggingOut = ref(false)

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
  <header class="app-header">
    <div class="page-container header-inner">
      <button class="brand" type="button" aria-label="Bảng điều khiển Veetee" @click="emit('selectTab', 'agents')">
        <RobotLogo :size="34" />
        <span>Bảng điều khiển Veetee</span>
      </button>

      <nav class="primary-nav" aria-label="Điều hướng chính">
        <button
          class="nav-item"
          :class="{ 'is-active': activeTab === 'agents' }"
          :aria-current="activeTab === 'agents' ? 'page' : undefined"
          type="button"
          title="Trợ lý"
          @click="emit('selectTab', 'agents')"
        >
          <Bot :size="17" stroke-width="1.8" />
          <span class="nav-label">Trợ lý</span>
        </button>

        <button
          class="nav-item"
          :class="{ 'is-active': activeTab === 'ota' }"
          :aria-current="activeTab === 'ota' ? 'page' : undefined"
          type="button"
          title="Firmware OTA"
          @click="emit('selectTab', 'ota')"
        >
          <Box :size="17" stroke-width="1.8" />
          <span class="nav-label">Firmware OTA</span>
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
  </header>
</template>
