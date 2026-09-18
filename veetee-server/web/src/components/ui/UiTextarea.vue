<script setup>
import { computed, useAttrs } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  label: { type: String, default: '' },
  hint: { type: String, default: '' },
  error: { type: String, default: '' },
  rows: { type: Number, default: 5 },
})
const emit = defineEmits(['update:modelValue'])
const attrs = useAttrs()
const inputId = computed(() => attrs.id || `ui-textarea-${Math.random().toString(36).slice(2, 9)}`)
</script>

<template>
  <label class="ui-field" :for="inputId">
    <span v-if="label" class="ui-field__label">{{ label }}</span>
    <textarea
      v-bind="attrs"
      :id="inputId"
      class="ui-textarea"
      :class="{ 'ui-textarea--error': error }"
      :rows="rows"
      :value="modelValue"
      @input="emit('update:modelValue', $event.target.value)"
    ></textarea>
    <span v-if="error" class="ui-field__error">{{ error }}</span>
    <span v-else-if="hint" class="ui-field__hint">{{ hint }}</span>
  </label>
</template>
