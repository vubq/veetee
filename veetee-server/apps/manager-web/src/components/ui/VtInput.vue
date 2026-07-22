<script setup lang="ts">
defineOptions({ inheritAttrs: false });

withDefaults(
  defineProps<{
    modelValue?: string | number;
    type?: string;
    invalid?: boolean;
  }>(),
  { modelValue: "", type: "text", invalid: false },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();
</script>

<template>
  <input
    v-bind="$attrs"
    class="vt-control vt-input"
    :class="{ 'is-invalid': invalid }"
    :type="type"
    :value="modelValue"
    :aria-invalid="invalid || undefined"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>
