<script setup lang="ts">
import { authState } from '@/api/controlPlane'
import AppShell from '@/layouts/AppShell.vue'
import AuthView from '@/views/AuthView.vue'
</script>

<template>
  <!-- Shell chỉ được dựng sau khi đã đăng nhập; key đảm bảo mỗi phiên có trạng thái riêng. -->
  <AuthView v-if="!authState.authenticated" />
  <AppShell v-else :key="authState.userEmail" />
</template>
