<script setup lang="ts">
defineProps<{ eyebrow: string; title: string; description: string }>();
</script>

<template>
  <header class="vt-page-header">
    <div><span class="vt-kicker">{{ eyebrow }}</span><h1>{{ title }}</h1><p>{{ description }}</p></div>
    <div v-if="$slots.actions" class="vt-page-actions"><slot name="actions" /></div>
  </header>
</template>
