<script setup>
defineProps({
  title: { type: String, default: '' },
  description: { type: String, default: '' },
})
</script>

<template>
  <div class="ui-empty-state">
    <div class="ui-empty-state__icon"><slot name="icon" /></div>
    <strong>{{ title }}</strong>
    <p>{{ description }}</p>
    <div v-if="$slots.default" class="ui-empty-state__action"><slot /></div>
  </div>
</template>
