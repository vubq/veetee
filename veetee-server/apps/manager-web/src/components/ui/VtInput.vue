<script setup lang="ts">
import { computed, inject, useAttrs } from "vue";

import { vtFieldContextKey } from "./field-context";

defineOptions({ inheritAttrs: false });

const props = withDefaults(
  defineProps<{
    modelValue?: string | number;
    type?: string;
    invalid?: boolean;
  }>(),
  { modelValue: "", type: "text", invalid: false },
);

const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const attrs = useAttrs();
const field = inject(vtFieldContextKey, undefined);
const controlId = computed(() => typeof attrs.id === "string" ? attrs.id : field?.controlId);
const describedBy = computed(() => typeof attrs["aria-describedby"] === "string" ? attrs["aria-describedby"] : field?.describedBy.value);
const isInvalid = computed(() => props.invalid || field?.invalid.value || undefined);
</script>

<template>
  <input
    v-bind="$attrs"
    :id="controlId"
    class="vt-control vt-input"
    :class="{ 'is-invalid': isInvalid }"
    :type="type"
    :value="modelValue"
    :aria-describedby="describedBy"
    :aria-invalid="isInvalid"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>
