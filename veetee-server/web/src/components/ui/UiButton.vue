<script setup>
import { computed } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'secondary' },
  size: { type: String, default: 'md' },
  iconOnly: Boolean,
  loading: Boolean,
  disabled: Boolean,
  type: { type: String, default: 'button' },
})

const classes = computed(() => [
  'ui-button',
  `ui-button--${props.variant}`,
  `ui-button--${props.size}`,
  props.iconOnly && 'ui-button--icon-only',
  props.loading && 'ui-button--loading',
])
</script>

<template>
  <button
    :type="type"
    :class="classes"
    :disabled="disabled || loading"
    :aria-busy="loading ? 'true' : undefined"
  >
    <span v-if="loading" class="ui-button__spinner" aria-hidden="true"></span>
    <slot v-else name="icon" />
    <span v-if="!iconOnly" class="ui-button__label"><slot /></span>
  </button>
</template>
