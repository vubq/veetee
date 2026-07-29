<script setup lang="ts">
import { computed, provide, useId } from "vue";

import { vtFieldContextKey } from "./field-context";

const props = defineProps<{
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  controlId?: string;
}>();

const generatedFieldId = `vt-field-${useId().replace(/:/g, "")}`;
const fieldId = props.controlId ?? generatedFieldId;
const descriptionId = `${fieldId}-description`;
const describedBy = computed(() => props.error || props.hint ? descriptionId : undefined);
const invalid = computed(() => Boolean(props.error));
provide(vtFieldContextKey, { controlId: fieldId, describedBy, invalid });
</script>

<template>
  <label class="vt-field" :class="{ 'has-error': invalid }" :for="fieldId">
    <span class="vt-field-label">
      {{ label }}
      <i v-if="required" aria-hidden="true">*</i>
    </span>
    <slot />
    <small v-if="error" :id="descriptionId" class="vt-field-error" role="alert">{{ error }}</small>
    <small v-else-if="hint" :id="descriptionId" class="vt-field-hint">{{ hint }}</small>
  </label>
</template>
