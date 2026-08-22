<script setup lang="ts">
import { ref } from 'vue'

import AppFooter from '@/components/AppFooter.vue'
import AppHeader from '@/components/AppHeader.vue'
import { useAuth } from '@/composables/auth'
import AgentsView from '@/views/AgentsView.vue'
import OtaView from '@/views/OtaView.vue'

type View = 'agents' | 'ota'
const activeView = ref<View>('agents')
const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const { authenticated, login, logout } = useAuth()

async function signIn() {
  busy.value = true; error.value = ''
  try { await login(email.value, password.value) }
  catch (reason) { error.value = reason instanceof Error ? reason.message : 'Đăng nhập thất bại.' }
  finally { busy.value = false }
}
</script>

<template>
  <div class="app-shell">
    <AppHeader v-if="authenticated" :active-view="activeView" @navigate="activeView = $event" @logout="logout" />
    <main v-if="!authenticated" class="page-container auth-page">
      <form class="auth-card" @submit.prevent="signIn">
        <h1>Đăng nhập Veetee Console</h1>
        <p>Phiên và access token chỉ được giữ trong bộ nhớ của tab này.</p>
        <label><span>Email</span><input v-model="email" class="text-input" type="email" required autocomplete="username" /></label>
        <label><span>Mật khẩu</span><input v-model="password" class="text-input" type="password" required minlength="12" autocomplete="current-password" /></label>
        <p v-if="error" class="form-status error">{{ error }}</p>
        <button class="button button-primary" type="submit" :disabled="busy">{{ busy ? 'Đang đăng nhập...' : 'Đăng nhập' }}</button>
      </form>
    </main>
    <AgentsView v-else-if="activeView === 'agents'" />
    <OtaView v-else />
    <AppFooter />
  </div>
</template>
