<script setup lang="ts">
defineProps<{
  label: string
  active?: boolean
}>()
</script>

<template>
  <button class="icon-button" :class="{ 'is-active': active }" type="button" :aria-label="label" :title="label">
    <slot />
  </button>
</template>
