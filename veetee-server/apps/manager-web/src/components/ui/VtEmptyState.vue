<script setup lang="ts">
import VtIcon from "./VtIcon.vue";

withDefaults(
  defineProps<{ icon?: "device" | "agent" | "resource" | "telemetry" | "tool" | "display" | "mic"; title: string; text: string }>(),
  { icon: "resource" },
);
</script>

<template>
  <div class="vt-empty-state">
    <span><VtIcon :name="icon" :size="24" /></span>
    <h3>{{ title }}</h3>
    <p>{{ text }}</p>
    <slot />
  </div>
</template>
