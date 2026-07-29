<script setup lang="ts">
withDefaults(defineProps<{ size?: "sm" | "md" | "lg" }>(), { size: "md" });
</script>

<template>
  <span class="vt-brand-mark" :class="`is-${size}`" aria-hidden="true"><i></i><i></i></span>
</template>
