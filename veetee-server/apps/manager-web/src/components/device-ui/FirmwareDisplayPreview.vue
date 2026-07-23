<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import {
  DEVICE_UI_TARGET,
  FIRMWARE_SCREEN_COPY,
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

function packRgb565(hex: string): number {
  const value = Number.parseInt(hex.slice(1), 16);
  const red = (value >> 16) & 0xff;
  const green = (value >> 8) & 0xff;
  const blue = value & 0xff;
  return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3);
}

function packedRgb565(packed: number): string {
  const r = ((packed >> 11) & 0x1f) * 255 / 31;
  const g = ((packed >> 5) & 0x3f) * 255 / 63;
  const b = (packed & 0x1f) * 255 / 31;
  return `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`;
}

function rgb565(hex: string): string {
  return packedRgb565(packRgb565(hex));
}

function mixRgb565(baseHex: string, overlayHex: string, overlayEighths: number): string {
  const base = packRgb565(baseHex);
  const overlay = packRgb565(overlayHex);
  const weight = Math.max(0, Math.min(8, overlayEighths));
  const inverse = 8 - weight;
  const red = ((((base >> 11) & 0x1f) * inverse) + (((overlay >> 11) & 0x1f) * weight)) >> 3;
  const green = ((((base >> 5) & 0x3f) * inverse) + (((overlay >> 5) & 0x3f) * weight)) >> 3;
  const blue = (((base & 0x1f) * inverse) + ((overlay & 0x1f) * weight)) >> 3;
  return packedRgb565((red << 11) | (green << 5) | blue);
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

let animationFrame = 0;
let animationTimer: number | undefined;

function render(): void {
  const context = canvas.value?.getContext("2d");
  if (!context) return;
  context.imageSmoothingEnabled = false;
  const background = rgb565(props.palette.background);
  const foreground = rgb565(props.palette.foreground);
  const accent = rgb565(props.palette.accent);
  const panel = mixRgb565(props.palette.background, props.palette.foreground, 1);
  const face = mixRgb565(props.palette.background, props.palette.foreground, 2);
  const accentSoft = mixRgb565(props.palette.background, props.palette.accent, 2);
  const copy = FIRMWARE_SCREEN_COPY[props.state];
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
  const roundedRectangle = (x: number, y: number, width: number, height: number, radius: number, color: string): void => {
    if (width <= 0 || height <= 0) return;
    const boundedRadius = Math.max(0, Math.min(radius, Math.floor(Math.min(width, height) / 2)));
    if (boundedRadius === 0) {
      rectangle(x, y, width, height, color);
      return;
    }
    rectangle(x + boundedRadius, y, width - boundedRadius * 2, height, color);
    rectangle(x, y + boundedRadius, width, height - boundedRadius * 2, color);
    circle(x + boundedRadius, y + boundedRadius, boundedRadius, color, true);
    circle(x + width - boundedRadius - 1, y + boundedRadius, boundedRadius, color, true);
    circle(x + boundedRadius, y + height - boundedRadius - 1, boundedRadius, color, true);
    circle(x + width - boundedRadius - 1, y + height - boundedRadius - 1, boundedRadius, color, true);
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
    roundedRectangle(5, 3, 230, 274, 29, face);
    roundedRectangle(8, 6, 224, 268, 26, background);

    text("POCKET OS", 20, 17, 1, accent);
    text(copy.number, 198, 17, 1, foreground);
    text(copy.kicker, 20, 42, 1, face);
    text(copy.title, 20, 59, 2, foreground);
    circle(205, 55, animationFrame % 2 === 0 ? 5 : 3, accent, true);

    roundedRectangle(18, 86, 204, 76, 20, accent);
    text("SYSTEM STATE", 30, 98, 1, background);
    if (activationCode) {
      text(activationCode, 30, 123, 3, background);
    } else {
      text(copy.number, 30, 121, 4, background);
    }
    circle(178, 124, 25, background, true);
    if (animationFrame % 8 === 7) {
      roundedRectangle(165, 123, 9, 4, 2, accent);
      roundedRectangle(182, 123, 9, 4, 2, accent);
    } else {
      const eyeHeight = props.state === "listening" ? 17 : 12;
      roundedRectangle(166, 124 - Math.floor(eyeHeight / 2), 7, eyeHeight, 3, accent);
      roundedRectangle(183, 124 - Math.floor(eyeHeight / 2), 7, eyeHeight, 3, accent);
    }

    const micStatus = props.state === "listening"
      ? "ACTIVE"
      : ["evaluating", "thinking", "speaking"].includes(props.state)
        ? "PAUSED"
        : "READY";
    roundedRectangle(18, 170, 98, 52, 15, panel);
    text("MIC", 30, 184, 2, foreground);
    text(micStatus, 30, 207, 1, face);
    roundedRectangle(124, 170, 98, 52, 15, panel);
    text("PHASE", 136, 184, 2, foreground);
    text(copy.number, 136, 207, 1, accent);

    centeredText(copy.hint, 229, 1, foreground);
    roundedRectangle(57, 246, 126, 22, 11, panel);
    const active = [
      props.state === "listening" || props.state === "evaluating",
      props.state === "thinking",
      props.state === "speaking",
    ];
    for (let item = 0; item < 3; item += 1) {
      const itemActive = active[item] ?? false;
      circle(
        84 + item * 36,
        257,
        itemActive && animationFrame % 2 === 0 ? 5 : 3,
        itemActive ? accent : accentSoft,
        true,
      );
    }
  } else if (props.composition === "monolith") {
    const bob = [0, -2, -1, 1][animationFrame % 4] ?? 0;
    const blink = animationFrame % 8 === 7 || props.state === "closing" || props.state === "aborting";
    text("VEE", 16, 18, 1, foreground);
    text(copy.kicker, 58, 18, 1, accent);
    text(copy.number, 204, 18, 1, foreground);
    roundedRectangle(16, 43, 208, 166, 28, panel);
    const headY = 61 + bob;
    line(120, headY - 9, 120, headY + 3, 3, foreground);
    circle(120, headY - 12, animationFrame % 2 === 0 ? 5 : 3, accent, true);
    roundedRectangle(82, headY + 76, 76, 49, 18, accent);
    roundedRectangle(99, headY + 87, 42, 27, 10, face);
    circle(120, headY + 100, animationFrame % 2 === 0 ? 6 : 4, foreground, true);
    roundedRectangle(90, headY + 118, 20, 10, 5, accent);
    roundedRectangle(130, headY + 118, 20, 10, 5, accent);

    const armY = headY + 92;
    if (props.state === "listening") {
      line(88, armY, 62, armY - 25, 7, accent);
      line(152, armY, 178, armY - 25, 7, accent);
      line(43, headY + 28, 34, headY + 18, 2, foreground);
      line(43, headY + 43, 30, headY + 43, 2, foreground);
      line(197, headY + 28, 206, headY + 18, 2, foreground);
      line(197, headY + 43, 210, headY + 43, 2, foreground);
    } else {
      line(88, armY, 67, armY + 15, 7, accent);
      line(152, armY, 173, armY + 15, 7, accent);
    }

    roundedRectangle(54, headY + 25, 18, 34, 8, accent);
    roundedRectangle(168, headY + 25, 18, 34, 8, accent);
    roundedRectangle(61, headY, 118, 88, 34, accent);
    roundedRectangle(72, headY + 13, 96, 56, 20, background);
    const eyeY = headY + 37;
    if (blink) {
      roundedRectangle(89, eyeY, 22, 5, 2, foreground);
      roundedRectangle(129, eyeY, 22, 5, 2, foreground);
    } else {
      let look = 0;
      if (props.state === "evaluating" || props.state === "thinking") look = animationFrame % 2 === 0 ? -3 : 3;
      roundedRectangle(89 + look, eyeY - 2, 22, 13, 6, foreground);
      roundedRectangle(129 + look, eyeY - 2, 22, 13, 6, foreground);
    }

    const mouthY = headY + 59;
    if (props.state === "speaking") {
      const mouthWidth = animationFrame % 2 === 0 ? 28 : 16;
      roundedRectangle(120 - Math.floor(mouthWidth / 2), mouthY - 3, mouthWidth, 7, 3, accent);
    } else if (props.state === "pairing_recovery") {
      line(109, mouthY + 3, 120, mouthY - 1, 2, accent);
      line(120, mouthY - 1, 131, mouthY + 3, 2, accent);
    } else {
      roundedRectangle(109, mouthY - 2, 22, 5, 2, accent);
    }
    if (activationCode) {
      roundedRectangle(32, 176, 176, 42, 14, face);
      centeredText(activationCode, 187, 3, foreground);
    } else {
      centeredText(copy.title, 220, 2, foreground);
    }
    centeredText(copy.hint, 254, 1, foreground);
  } else {
    circle(19, 21, 4, accent, true);
    text("VEE TEE", 31, 17, 1, foreground);
    text(copy.number, 204, 17, 1, accent);
    roundedRectangle(14, 47, 212, 158, 30, panel);
    const closing = props.state === "closing" || props.state === "aborting";
    const blink = closing || animationFrame % 8 === 7;
    let eyeHeight = blink ? 6 : 54;
    let eyeWidth = 28;
    let eyeY = 111;
    if (props.state === "listening") {
      eyeHeight = blink ? 7 : 62;
      eyeWidth = 31;
      eyeY += animationFrame % 2 === 0 ? -2 : 2;
    }
    if (props.state === "speaking" && !blink) eyeHeight = animationFrame % 2 === 0 ? 46 : 58;
    let look = 0;
    if (props.state === "evaluating" || props.state === "thinking") look = animationFrame % 2 === 0 ? -7 : 7;
    roundedRectangle(78 - Math.floor(eyeWidth / 2) + look, eyeY - Math.floor(eyeHeight / 2), eyeWidth, eyeHeight, Math.max(2, Math.floor(eyeWidth / 2)), accent);
    roundedRectangle(162 - Math.floor(eyeWidth / 2) + look, eyeY - Math.floor(eyeHeight / 2), eyeWidth, eyeHeight, Math.max(2, Math.floor(eyeWidth / 2)), accent);
    if (props.state === "listening" && !blink) {
      line(40, 88, 32, 80, 2, accentSoft);
      line(40, 111, 28, 111, 2, accentSoft);
      line(200, 88, 208, 80, 2, accentSoft);
      line(200, 111, 212, 111, 2, accentSoft);
    }
    if (activationCode) {
      roundedRectangle(55, 158, 130, 33, 12, accentSoft);
      centeredText(activationCode, 165, 3, foreground);
    } else {
      let mouthWidth = 24;
      let mouthHeight = 5;
      if (props.state === "speaking") {
        mouthWidth = animationFrame % 2 === 0 ? 34 : 22;
        mouthHeight = animationFrame % 2 === 0 ? 6 : 13;
      }
      roundedRectangle(120 - Math.floor(mouthWidth / 2), 165 - Math.floor(mouthHeight / 2), mouthWidth, mouthHeight, Math.max(2, Math.floor(mouthHeight / 2)), accent);
    }
    centeredText(copy.title, 220, 2, foreground);
    centeredText(copy.hint, 254, 1, foreground);
  }
}

onMounted(() => {
  render();
  animationTimer = window.setInterval(() => {
    animationFrame += 1;
    render();
  }, 500);
});
onBeforeUnmount(() => window.clearInterval(animationTimer));
watch(
  () => [props.composition, props.state, props.palette, props.activationCode],
  async () => {
    animationFrame = 0;
    await nextTick();
    render();
  },
  { deep: true },
);
</script>

<template>
  <canvas ref="canvas" class="firmware-display-canvas" :width="DEVICE_UI_TARGET.width" :height="DEVICE_UI_TARGET.height" :aria-label="`${composition} · ${state}`"></canvas>
</template>
