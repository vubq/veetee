<script setup lang="ts">
import { ShieldAlert } from '@lucide/vue'

defineProps<{
  title?: string
  description?: string
}>()
</script>

<template>
  <div class="role-gate-card" data-testid="role-gate">
    <ShieldAlert :size="36" class="role-gate-icon" />
    <h3>{{ title || 'Cần quyền admin' }}</h3>
    <p>{{ description || 'Tài khoản của bạn không có quyền thực hiện thao tác hoặc xem nội dung này. Vui lòng liên hệ quản trị viên để biết thêm chi tiết.' }}</p>
  </div>
</template>
