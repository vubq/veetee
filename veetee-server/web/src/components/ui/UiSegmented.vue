<script setup>
const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  options: { type: Array, default: () => [] },
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue', 'change'])
function normalize(option) {
  return option && typeof option === 'object'
    ? { value: option.value, label: option.label ?? String(option.value) }
    : { value: option, label: String(option) }
}
function choose(value) {
  if (props.disabled) return
  emit('update:modelValue', value)
  emit('change', value)
}
</script>

<template>
  <div class="ui-segmented" :class="{ 'ui-segmented--disabled': disabled }" role="radiogroup">
    <button
      v-for="raw in options"
      :key="String(normalize(raw).value)"
      type="button"
      class="ui-segmented__item"
      :class="{ 'ui-segmented__item--active': normalize(raw).value === modelValue }"
      :aria-checked="normalize(raw).value === modelValue"
      role="radio"
      @click="choose(normalize(raw).value)"
    >
      {{ normalize(raw).label }}
    </button>
  </div>
</template>
