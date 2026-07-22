<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: "primary" | "secondary" | "danger" | "quiet";
    size?: "sm" | "md" | "lg";
    type?: "button" | "submit" | "reset";
    busy?: boolean;
  }>(),
  { variant: "primary", size: "md", type: "button", busy: false },
);
</script>

<template>
  <button
    class="vt-button"
    :class="[`is-${variant}`, `is-${size}`]"
    :type="type"
    :disabled="busy || $attrs.disabled === true || $attrs.disabled === ''"
    :aria-busy="busy || undefined"
  >
    <span v-if="busy" class="vt-spinner" aria-hidden="true"></span>
    <slot />
  </button>
</template>
