<script setup lang="ts">
import { Switch, SwitchDescription, SwitchGroup, SwitchLabel } from "@headlessui/vue";

defineProps<{ modelValue: boolean; label: string; description?: string }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();
</script>

<template>
  <SwitchGroup as="div" class="vt-switch-group">
    <div><SwitchLabel class="vt-switch-label">{{ label }}</SwitchLabel><SwitchDescription v-if="description" class="vt-switch-description">{{ description }}</SwitchDescription></div>
    <Switch :model-value="modelValue" class="vt-switch" @update:model-value="emit('update:modelValue', $event)"><span aria-hidden="true"></span></Switch>
  </SwitchGroup>
</template>
