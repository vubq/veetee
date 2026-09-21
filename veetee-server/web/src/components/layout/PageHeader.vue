<script setup>
defineProps({
  eyebrow: { type: String, default: '' },
  title: { type: String, default: '' },
  description: { type: String, default: '' },
})
</script>

<template>
  <header class="page-header-card">
    <div class="page-header-card__copy">
      <span v-if="eyebrow" class="page-header-card__eyebrow">{{ eyebrow }}</span>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header-card__actions">
      <slot name="actions" />
    </div>
  </header>
</template>
