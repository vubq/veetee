<script setup lang="ts">
withDefaults(
  defineProps<{ tone?: "neutral" | "success" | "warning" | "danger" | "info"; dot?: boolean }>(),
  { tone: "neutral", dot: false },
);
</script>

<template>
  <span class="vt-badge" :class="`is-${tone}`"><i v-if="dot"></i><slot /></span>
</template>
