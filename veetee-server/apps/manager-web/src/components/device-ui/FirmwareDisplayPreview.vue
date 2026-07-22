<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";

import {
  DEVICE_UI_TARGET,
  FIRMWARE_SCREEN_COPY,
  FIRMWARE_STATE_IDS,
  type FirmwareComposition,
  type FirmwareStateId,
} from "../../device-ui/firmware-contract";

const props = defineProps<{
  composition: FirmwareComposition;
  state: FirmwareStateId;
  palette: { background: string; foreground: string; accent: string };
  activationCode?: string;
}>();

const canvas = ref<HTMLCanvasElement>();

const FONT: number[][] = [
  [0x3e,0x51,0x49,0x45,0x3e],[0x00,0x42,0x7f,0x40,0x00],[0x42,0x61,0x51,0x49,0x46],[0x21,0x41,0x45,0x4b,0x31],[0x18,0x14,0x12,0x7f,0x10],
  [0x27,0x45,0x45,0x45,0x39],[0x3c,0x4a,0x49,0x49,0x30],[0x01,0x71,0x09,0x05,0x03],[0x36,0x49,0x49,0x49,0x36],[0x06,0x49,0x49,0x29,0x1e],
  [0x7e,0x11,0x11,0x11,0x7e],[0x7f,0x49,0x49,0x49,0x36],[0x3e,0x41,0x41,0x41,0x22],[0x7f,0x41,0x41,0x22,0x1c],[0x7f,0x49,0x49,0x49,0x41],
  [0x7f,0x09,0x09,0x09,0x01],[0x3e,0x41,0x49,0x49,0x7a],[0x7f,0x08,0x08,0x08,0x7f],[0x00,0x41,0x7f,0x41,0x00],[0x20,0x40,0x41,0x3f,0x01],
  [0x7f,0x08,0x14,0x22,0x41],[0x7f,0x40,0x40,0x40,0x40],[0x7f,0x02,0x0c,0x02,0x7f],[0x7f,0x04,0x08,0x10,0x7f],[0x3e,0x41,0x41,0x41,0x3e],
  [0x7f,0x09,0x09,0x09,0x06],[0x3e,0x41,0x51,0x21,0x5e],[0x7f,0x09,0x19,0x29,0x46],[0x46,0x49,0x49,0x49,0x31],[0x01,0x01,0x7f,0x01,0x01],
  [0x3f,0x40,0x40,0x40,0x3f],[0x1f,0x20,0x40,0x20,0x1f],[0x3f,0x40,0x38,0x40,0x3f],[0x63,0x14,0x08,0x14,0x63],[0x07,0x08,0x70,0x08,0x07],[0x61,0x51,0x49,0x45,0x43],
];

function rgb565(hex: string): string {
  const value = Number.parseInt(hex.slice(1), 16);
  const red = (value >> 16) & 0xff;
  const green = (value >> 8) & 0xff;
  const blue = value & 0xff;
  const packed = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3);
  const r = ((packed >> 11) & 0x1f) * 255 / 31;
  const g = ((packed >> 5) & 0x3f) * 255 / 63;
  const b = (packed & 0x1f) * 255 / 31;
  return `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`;
}

function glyph(character: string): number[] | undefined {
  const upper = character.toUpperCase();
  const code = upper.charCodeAt(0);
  if (code >= 48 && code <= 57) return FONT[code - 48];
  if (code >= 65 && code <= 90) return FONT[10 + code - 65];
  if (upper === "-") return [0x08,0x08,0x08,0x08,0x08];
  if (upper === ".") return [0x00,0x60,0x60,0x00,0x00];
  if (upper === ":") return [0x00,0x36,0x36,0x00,0x00];
  if (upper === "/") return [0x40,0x30,0x0c,0x03,0x00];
  return undefined;
}

function render(): void {
  const context = canvas.value?.getContext("2d");
  if (!context) return;
  context.imageSmoothingEnabled = false;
  const background = rgb565(props.palette.background);
  const foreground = rgb565(props.palette.foreground);
  const accent = rgb565(props.palette.accent);
  const copy = FIRMWARE_SCREEN_COPY[props.state];
  const stateIndex = FIRMWARE_STATE_IDS.indexOf(props.state);
  const activationCode = props.state === "activating" ? (props.activationCode ?? "284716") : undefined;

  const rectangle = (x: number, y: number, width: number, height: number, color: string): void => {
    context.fillStyle = color;
    context.fillRect(x, y, width, height);
  };
  const circle = (centerX: number, centerY: number, radius: number, color: string, filled: boolean): void => {
    context.fillStyle = color;
    const outerSquared = radius * radius;
    const innerRadius = Math.max(0, radius - 2);
    const innerSquared = innerRadius * innerRadius;
    for (let y = centerY - radius; y <= centerY + radius; y += 1) {
      for (let x = centerX - radius; x <= centerX + radius; x += 1) {
        const dx = x - centerX;
        const dy = y - centerY;
        const distance = dx * dx + dy * dy;
        if (distance <= outerSquared && (filled || distance >= innerSquared)) context.fillRect(x, y, 1, 1);
      }
    }
  };
  const line = (startX: number, startY: number, endX: number, endY: number, thickness: number, color: string): void => {
    let x0 = startX; let y0 = startY;
    const dx = Math.abs(endX - x0); const stepX = x0 < endX ? 1 : -1;
    const dy = -Math.abs(endY - y0); const stepY = y0 < endY ? 1 : -1;
    let error = dx + dy;
    while (true) {
      rectangle(x0 - Math.floor(thickness / 2), y0 - Math.floor(thickness / 2), thickness, thickness, color);
      if (x0 === endX && y0 === endY) break;
      const doubled = 2 * error;
      if (doubled >= dy) { error += dy; x0 += stepX; }
      if (doubled <= dx) { error += dx; y0 += stepY; }
    }
  };
  const drawGlyph = (character: string, x: number, y: number, scale: number, color: string): void => {
    if (character === " ") return;
    const bitmap = glyph(character);
    if (!bitmap) return;
    for (let column = 0; column < 5; column += 1) {
      for (let row = 0; row < 7; row += 1) {
        if (((bitmap[column] ?? 0) & (1 << row)) !== 0) rectangle(x + column * scale, y + row * scale, scale, scale, color);
      }
    }
  };
  const text = (value: string, x: number, y: number, scale: number, color: string): void => {
    for (const character of value) { drawGlyph(character, x, y, scale, color); x += 6 * scale; }
  };
  const centeredText = (value: string, y: number, preferredScale: number, color: string): void => {
    let scale = preferredScale;
    while (scale > 1 && value.length * 6 * scale > DEVICE_UI_TARGET.width - 20) scale -= 1;
    const width = Math.max(0, value.length * 6 * scale - scale);
    text(value, Math.max(0, Math.floor((DEVICE_UI_TARGET.width - width) / 2)), y, scale, color);
  };

  rectangle(0, 0, DEVICE_UI_TARGET.width, DEVICE_UI_TARGET.height, background);
  if (props.composition === "signal") {
    rectangle(16, 18, 30, 3, accent); text("VEE/TEE", 54, 13, 1, foreground);
    text(copy.number, 16, 48, 2, accent); text(copy.kicker, 58, 53, 1, foreground); centeredText(copy.title, 76, 3, foreground);
    const centerX = 120; const centerY = 162;
    if (activationCode) { circle(centerX, centerY, 57, accent, false); circle(centerX, centerY, 49, foreground, false); centeredText(activationCode, centerY - 12, 4, accent); }
    else {
      circle(centerX, centerY, 53, accent, false); circle(centerX, centerY, 40, foreground, false); circle(centerX, centerY, 17, accent, true);
      for (let segment = 0; segment < 7; segment += 1) {
        const x0 = centerX - 76 + segment * 24; const amplitude = 5 + ((stateIndex + segment) % 4) * 4;
        line(x0, centerY, x0 + 12, centerY - amplitude, 2, foreground); line(x0 + 12, centerY - amplitude, x0 + 24, centerY, 2, foreground);
      }
    }
    rectangle(16, 239, DEVICE_UI_TARGET.width - 32, 1, accent); centeredText(copy.hint, 255, 1, foreground);
  } else if (props.composition === "monolith") {
    rectangle(0, 0, 9, DEVICE_UI_TARGET.height, accent); text("VEE/TEE", 24, 20, 2, foreground); text(copy.number, 24, 62, 4, accent);
    text(copy.kicker, 24, 104, 1, foreground); text(copy.title, 24, 126, 3, foreground);
    if (activationCode) text(activationCode, 24, 188, 4, accent);
    else for (let bar = 0; bar < 9; bar += 1) { const height = 10 + ((bar + stateIndex) % 5) * 11; rectangle(24 + bar * 20, 218 - height, 11, height, bar % 2 === 0 ? accent : foreground); }
    text(copy.hint, 24, 255, 1, foreground);
  } else {
    text("VEE TEE", 18, 18, 1, foreground); text(copy.number, DEVICE_UI_TARGET.width - 42, 18, 1, accent);
    const centerX = 120; const centerY = 137; circle(centerX, centerY, 58, accent, false); circle(centerX, centerY, 40, foreground, false);
    if (activationCode) centeredText(activationCode, centerY - 10, 3, accent); else circle(centerX, centerY, 11, accent, true);
    centeredText(copy.title, 210, 2, foreground); centeredText(copy.hint, 254, 1, foreground);
  }
}

onMounted(render);
watch(() => [props.composition, props.state, props.palette, props.activationCode], async () => { await nextTick(); render(); }, { deep: true });
</script>

<template>
  <canvas ref="canvas" class="firmware-display-canvas" :width="DEVICE_UI_TARGET.width" :height="DEVICE_UI_TARGET.height" :aria-label="`${composition} · ${state}`"></canvas>
</template>
