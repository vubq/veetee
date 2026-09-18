<script setup>
const props = defineProps({
  tone: { type: String, default: 'neutral' },
  dot: Boolean,
})
</script>

<template>
  <span class="ui-badge" :class="`ui-badge--${tone}`">
    <span v-if="dot" class="ui-badge__dot"></span>
    <slot />
  </span>
</template>
