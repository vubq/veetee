<script lang="ts">
export type VtIconName =
  | "overview" | "device" | "display" | "agent" | "provider" | "lab" | "tool" | "resource"
  | "telemetry" | "plus" | "arrow" | "menu" | "close" | "check" | "warning" | "refresh"
  | "logout" | "upload" | "play" | "stop" | "mic" | "edit" | "search" | "chevron";
</script>

<script setup lang="ts">
defineProps<{
  name: VtIconName;
  size?: number;
}>();
</script>

<template>
  <svg
    class="vt-icon"
    :width="size ?? 20"
    :height="size ?? 20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <template v-if="name === 'overview'">
      <rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" />
    </template>
    <template v-else-if="name === 'device'">
      <rect x="5" y="2.5" width="14" height="19" rx="4" /><path d="M9 6h6M10 18h4" /><circle cx="9" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="15" cy="12" r="1" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="name === 'display'">
      <rect x="3" y="4" width="18" height="13" rx="3" /><path d="M8 21h8M12 17v4M7 9h.01M11 9h6M7 12h10" />
    </template>
    <template v-else-if="name === 'agent'">
      <circle cx="12" cy="8" r="4" /><path d="M4.5 21a7.5 7.5 0 0 1 15 0M18.5 5.5l1-1M5.5 5.5l-1-1" />
    </template>
    <template v-else-if="name === 'provider'">
      <path d="M12 3v5M12 16v5M4.2 7.5l4.3 2.4M15.5 14.1l4.3 2.4M4.2 16.5l4.3-2.4M15.5 9.9l4.3-2.4" /><circle cx="12" cy="12" r="4" />
    </template>
    <template v-else-if="name === 'lab'">
      <path d="M5 3v5l-2 9a3 3 0 0 0 3 4h12a3 3 0 0 0 3-4l-2-9V3M8 3h8M7 13h10" /><circle cx="10" cy="17" r="1" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="name === 'tool'">
      <path d="M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-2.5 2.5-3-3z" />
    </template>
    <template v-else-if="name === 'resource'">
      <path d="M12 3 4 7l8 4 8-4-8-4Z" /><path d="m4 12 8 4 8-4M4 17l8 4 8-4" />
    </template>
    <template v-else-if="name === 'telemetry'">
      <path d="M3 12h4l2.5-6 4.2 12 2.5-6H21" />
    </template>
    <template v-else-if="name === 'plus'"><path d="M12 5v14M5 12h14" /></template>
    <template v-else-if="name === 'arrow'"><path d="M5 12h14M14 7l5 5-5 5" /></template>
    <template v-else-if="name === 'menu'"><path d="M4 7h16M4 12h16M4 17h16" /></template>
    <template v-else-if="name === 'close'"><path d="m6 6 12 12M18 6 6 18" /></template>
    <template v-else-if="name === 'check'"><path d="m5 12 4 4L19 6" /></template>
    <template v-else-if="name === 'warning'"><path d="M12 3 2.8 19h18.4L12 3Z" /><path d="M12 9v4M12 17h.01" /></template>
    <template v-else-if="name === 'refresh'"><path d="M20 7v5h-5M4 17v-5h5" /><path d="M7 19a8 8 0 0 0 13-7M17 5A8 8 0 0 0 4 12" /></template>
    <template v-else-if="name === 'logout'"><path d="M10 17l5-5-5-5M15 12H3M14 3h5a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-5" /></template>
    <template v-else-if="name === 'upload'"><path d="M12 16V4M7 9l5-5 5 5M4 15v4h16v-4" /></template>
    <template v-else-if="name === 'play'"><path d="m8 5 11 7-11 7V5Z" /></template>
    <template v-else-if="name === 'stop'"><rect x="6" y="6" width="12" height="12" rx="2" /></template>
    <template v-else-if="name === 'mic'"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" /></template>
    <template v-else-if="name === 'edit'"><path d="m4 16-1 5 5-1L19 9l-4-4L4 16ZM13.5 6.5l4 4" /></template>
    <template v-else-if="name === 'search'"><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></template>
    <template v-else><path d="m8 10 4 4 4-4" /></template>
  </svg>
</template>
