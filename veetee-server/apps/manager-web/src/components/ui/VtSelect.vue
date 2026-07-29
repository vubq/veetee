<script setup lang="ts">
import { computed, inject, useAttrs } from "vue";

import { vtFieldContextKey } from "./field-context";

defineOptions({ inheritAttrs: false });

const props = withDefaults(defineProps<{ modelValue?: string | number; invalid?: boolean }>(), { modelValue: "", invalid: false });
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const attrs = useAttrs();
const field = inject(vtFieldContextKey, undefined);
const controlId = computed(() => typeof attrs.id === "string" ? attrs.id : field?.controlId);
const describedBy = computed(() => typeof attrs["aria-describedby"] === "string" ? attrs["aria-describedby"] : field?.describedBy.value);
const isInvalid = computed(() => props.invalid || field?.invalid.value || undefined);
</script>

<template>
  <select v-bind="$attrs" :id="controlId" class="vt-control vt-select" :class="{ 'is-invalid': isInvalid }" :value="modelValue" :aria-describedby="describedBy" :aria-invalid="isInvalid" @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"><slot /></select>
</template>
