<script setup lang="ts">
import { useId } from 'vue'

withDefaults(
  defineProps<{
    size?: number
    label?: string
  }>(),
  {
    size: 34,
    label: 'Veetee',
  },
)

const id = useId()
const shellGradientId = `${id}-shell`
const faceGradientId = `${id}-face`
const eyeGradientId = `${id}-eyes`
const eyeGlowId = `${id}-glow`
</script>

<template>
  <svg
    class="robot-logo"
    :width="size"
    :height="size"
    viewBox="0 0 48 48"
    fill="none"
    role="img"
    :aria-label="label"
  >
    <defs>
      <linearGradient :id="shellGradientId" x1="7" y1="6" x2="41" y2="42" gradientUnits="userSpaceOnUse">
        <stop stop-color="#335e62" />
        <stop offset="0.48" stop-color="#234a50" />
        <stop offset="1" stop-color="#17383f" />
      </linearGradient>
      <linearGradient :id="faceGradientId" x1="11" y1="9" x2="37" y2="39" gradientUnits="userSpaceOnUse">
        <stop stop-color="#17373e" />
        <stop offset="1" stop-color="#0d292f" />
      </linearGradient>
      <linearGradient :id="eyeGradientId" x1="17" y1="18" x2="31" y2="29" gradientUnits="userSpaceOnUse">
        <stop stop-color="#d7ff91" />
        <stop offset="0.45" stop-color="#c8f474" />
        <stop offset="1" stop-color="#9fdc4f" />
      </linearGradient>
      <filter :id="eyeGlowId" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="1.2" result="blur" />
        <feMerge>
          <feMergeNode in="blur" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
    </defs>
    <rect x="4" y="5" width="40" height="38" rx="9" :fill="`url(#${shellGradientId})`" />
    <rect
      x="7.5"
      y="8.5"
      width="33"
      height="31"
      rx="6.5"
      :fill="`url(#${faceGradientId})`"
      stroke="#41676a"
      stroke-width="1"
    />
    <path d="M12.8 12c3-1.2 6.7-1.7 11.2-1.7s8.2.5 11.2 1.7" stroke="#52777a" stroke-width="1" stroke-linecap="round" opacity=".42" />
    <g :filter="`url(#${eyeGlowId})`">
      <ellipse cx="17.8" cy="23.4" rx="3.4" ry="5.2" :fill="`url(#${eyeGradientId})`" />
      <ellipse cx="30.2" cy="23.4" rx="3.4" ry="5.2" :fill="`url(#${eyeGradientId})`" />
    </g>
    <ellipse cx="16.8" cy="21.4" rx=".8" ry="1.4" fill="#f1ffc8" opacity=".82" />
    <ellipse cx="29.2" cy="21.4" rx=".8" ry="1.4" fill="#f1ffc8" opacity=".82" />
  </svg>
</template>
