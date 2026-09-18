<script setup>
const props = defineProps({
  modelValue: Boolean,
  label: { type: String, default: '' },
  description: { type: String, default: '' },
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue', 'change'])
function toggle() {
  if (props.disabled) return
  const value = !props.modelValue
  emit('update:modelValue', value)
  emit('change', value)
}
</script>

<template>
  <button type="button" class="ui-switch-row" :disabled="disabled" @click="toggle">
    <span v-if="label || description" class="ui-switch-row__copy">
      <strong v-if="label">{{ label }}</strong>
      <small v-if="description">{{ description }}</small>
    </span>
    <span class="ui-switch" :class="{ 'ui-switch--on': modelValue }" role="switch" :aria-checked="modelValue">
      <span class="ui-switch__thumb"></span>
    </span>
  </button>
</template>
