<script setup lang="ts">
import { ref } from 'vue'
import RobotLogo from '@/components/RobotLogo.vue'
import { authState, login } from '@/api/controlPlane'

const email = ref('')
const password = ref('')

async function handleSubmit() {
  if (!email.value || !password.value || authState.loggingIn) return
  try {
    await login(email.value.trim(), password.value)
  } catch {
    // authState.loginError được cập nhật trong module API controlPlane.
  }
}

function clearError() {
  authState.loginError = ''
}
</script>

<template>
  <main class="auth-page">
    <div class="auth-card">
      <div class="auth-header">
        <RobotLogo :size="48" />
        <h1>Bảng điều khiển Veetee</h1>
        <p>Đăng nhập để quản lý hệ thống và trợ lý thông minh</p>
      </div>

      <form class="auth-form" @submit.prevent="handleSubmit">
        <div v-if="authState.logoutWarning" class="auth-warning" role="status">
          {{ authState.logoutWarning }}
        </div>
        <label class="auth-field">
          <span>Email đăng nhập</span>
          <input
            v-model="email"
            name="email"
            type="email"
            placeholder="you@example.com"
            required
            autocomplete="username"
            data-testid="auth-email"
            :disabled="authState.loggingIn"
            @input="clearError"
          />
        </label>

        <label class="auth-field">
          <span>Mật khẩu</span>
          <input
            v-model="password"
            name="password"
            type="password"
            placeholder="Nhập mật khẩu"
            required
            autocomplete="current-password"
            data-testid="auth-password"
            :disabled="authState.loggingIn"
            @input="clearError"
          />
        </label>

        <div v-if="authState.loginError" class="auth-error" role="alert" data-testid="auth-error">
          {{ authState.loginError }}
        </div>

        <button
          type="submit"
          class="button button-primary auth-submit"
          data-testid="auth-submit"
          :disabled="authState.loggingIn || !email || !password"
        >
          {{ authState.loggingIn ? 'Đang đăng nhập...' : 'Đăng nhập' }}
        </button>
      </form>
    </div>
  </main>
</template>
