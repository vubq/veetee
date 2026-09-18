<script setup>
defineProps({
  eyebrow: { type: String, default: '' },
  title: { type: String, default: '' },
  description: { type: String, default: '' },
})
</script>

<template>
  <header class="page-lead">
    <div class="page-lead__marker">
      <span>{{ eyebrow || 'VEETEE' }}</span>
    </div>
    <div class="page-lead__body">
      <div class="page-lead__copy">
        <h1>{{ title }}</h1>
        <p v-if="description">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="page-lead__actions"><slot name="actions" /></div>
    </div>
  </header>
</template>