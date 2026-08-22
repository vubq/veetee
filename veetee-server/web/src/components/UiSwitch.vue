<script setup lang="ts">
defineProps<{ modelValue: boolean; label: string }>()
defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>

<template>
  <button
    class="switch-control"
    :class="{ checked: modelValue }"
    type="button"
    role="switch"
    :aria-label="label"
    :aria-checked="modelValue"
    @click="$emit('update:modelValue', !modelValue)"
  ><span></span></button>
</template>
