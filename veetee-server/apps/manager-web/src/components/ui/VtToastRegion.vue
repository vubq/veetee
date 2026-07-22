<script setup lang="ts">
import VtIcon from "./VtIcon.vue";

defineProps<{ items: Array<{ id: number; message: string; tone: "success" | "danger" | "info" }> }>();
const emit = defineEmits<{ dismiss: [id: number] }>();
</script>

<template>
  <Teleport to="body">
    <div class="vt-toast-region" aria-live="polite">
      <TransitionGroup name="toast">
        <div v-for="item in items" :key="item.id" class="vt-toast" :class="`is-${item.tone}`">
          <span><VtIcon :name="item.tone === 'danger' ? 'warning' : 'check'" :size="17" /></span>
          <p>{{ item.message }}</p>
          <button type="button" aria-label="Đóng thông báo" @click="emit('dismiss', item.id)"><VtIcon name="close" :size="16" /></button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>
