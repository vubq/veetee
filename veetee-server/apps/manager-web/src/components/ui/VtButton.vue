<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: "primary" | "secondary" | "danger" | "quiet";
    type?: "button" | "submit" | "reset";
    busy?: boolean;
  }>(),
  { variant: "primary", type: "button", busy: false },
);
</script>

<template>
  <button
    class="vt-button"
    :class="`is-${variant}`"
    :type="type"
    :disabled="busy || $attrs.disabled === true || $attrs.disabled === ''"
    :aria-busy="busy || undefined"
  >
    <span v-if="busy" class="vt-button-spinner" aria-hidden="true"></span>
    <slot />
  </button>
</template>
