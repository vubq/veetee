<script setup>
import { computed, useAttrs } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  label: { type: String, default: '' },
  hint: { type: String, default: '' },
  error: { type: String, default: '' },
  prefix: { type: String, default: '' },
  suffix: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'change'])
const attrs = useAttrs()
const inputId = computed(() => attrs.id || `ui-input-${Math.random().toString(36).slice(2, 9)}`)
</script>

<template>
  <label class="ui-field" :for="inputId">
    <span v-if="label" class="ui-field__label">{{ label }}</span>
    <span class="ui-control" :class="{ 'ui-control--error': error }">
      <span v-if="prefix" class="ui-control__affix">{{ prefix }}</span>
      <input
        v-bind="attrs"
        :id="inputId"
        class="ui-control__input"
        :value="modelValue"
        @input="emit('update:modelValue', $event.target.value)"
        @change="emit('change', $event)"
      >
      <span v-if="suffix" class="ui-control__affix">{{ suffix }}</span>
    </span>
    <span v-if="error" class="ui-field__error">{{ error }}</span>
    <span v-else-if="hint" class="ui-field__hint">{{ hint }}</span>
  </label>
</template>
