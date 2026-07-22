<script setup lang="ts">
defineProps<{
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
}>();
</script>

<template>
  <label class="vt-field" :class="{ 'has-error': error }">
    <span class="vt-field-label">
      {{ label }}
      <i v-if="required" aria-hidden="true">*</i>
    </span>
    <slot />
    <small v-if="error" class="vt-field-error" role="alert">{{ error }}</small>
    <small v-else-if="hint" class="vt-field-hint">{{ hint }}</small>
  </label>
</template>
