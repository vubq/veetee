<script setup>
defineProps({
  padded: { type: Boolean, default: true },
  hoverable: Boolean,
})
</script>

<template>
  <section class="ui-card" :class="{ 'ui-card--padded': padded, 'ui-card--hoverable': hoverable }">
    <slot />
  </section>
</template>
