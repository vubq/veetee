// Panel truth: everything is drawn once at exactly 240x280 logical pixels,
// quantized to RGB565 the way the ST7789 framebuffer stores it, then scaled
// with nearest-neighbour so the preview never invents subpixel detail the
// panel cannot show.

import { PANEL } from "./contract.js";

// 4x4 Bayer matrix, normalised to [-0.5, 0.5).
const BAYER = [
  [0, 8, 2, 10],
  [12, 4, 14, 6],
  [3, 11, 1, 9],
  [15, 7, 13, 5],
].map((row) => row.map((value) => value / 16 - 0.5));

export class Panel {
  constructor(width = PANEL.width, height = PANEL.height) {
    this.width = width;
    this.height = height;
    this.canvas = document.createElement("canvas");
    this.canvas.width = width;
    this.canvas.height = height;
    this.ctx = this.canvas.getContext("2d", { willReadFrequently: true });
    this.ctx.textRendering = "geometricPrecision";
  }

  begin(backgroundColor) {
    const { ctx } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = backgroundColor;
    ctx.fillRect(0, 0, this.width, this.height);
  }

  // Truncating to 5/6/5 is what the framebuffer write actually does. Dithering
  // is optional because firmware would have to opt into it per gradient.
  quantize({ dither = false } = {}) {
    const image = this.ctx.getImageData(0, 0, this.width, this.height);
    const { data } = image;
    for (let y = 0; y < this.height; y += 1) {
      for (let x = 0; x < this.width; x += 1) {
        const index = (y * this.width + x) * 4;
        const bias = dither ? BAYER[y & 3][x & 3] : 0;
        data[index] = quantizeChannel(data[index], 31, bias);
        data[index + 1] = quantizeChannel(data[index + 1], 63, bias);
        data[index + 2] = quantizeChannel(data[index + 2], 31, bias);
        data[index + 3] = 255;
      }
    }
    this.ctx.putImageData(image, 0, 0);
  }

  present(target, { scale = 1, grid = false, gridColor = "rgba(0, 0, 0, 0.16)" } = {}) {
    const context = target.getContext("2d");
    target.width = this.width * scale;
    target.height = this.height * scale;
    context.imageSmoothingEnabled = false;
    context.clearRect(0, 0, target.width, target.height);
    context.drawImage(this.canvas, 0, 0, target.width, target.height);
    if (!grid || scale < 3) return;
    context.strokeStyle = gridColor;
    context.lineWidth = 1;
    context.beginPath();
    for (let x = 1; x < this.width; x += 1) {
      context.moveTo(x * scale + 0.5, 0);
      context.lineTo(x * scale + 0.5, target.height);
    }
    for (let y = 1; y < this.height; y += 1) {
      context.moveTo(0, y * scale + 0.5);
      context.lineTo(target.width, y * scale + 0.5);
    }
    context.stroke();
  }
}

function quantizeChannel(value, levels, bias) {
  const scaled = (value / 255) * levels + bias;
  const step = Math.max(0, Math.min(levels, Math.round(scaled)));
  return Math.round((step / levels) * 255);
}

export function packRgb565(red, green, blue) {
  return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3);
}

export function unpackRgb565(packed) {
  const red = (packed >> 11) & 0x1f;
  const green = (packed >> 5) & 0x3f;
  const blue = packed & 0x1f;
  return [
    Math.round((red * 255) / 31),
    Math.round((green * 255) / 63),
    Math.round((blue * 255) / 31),
  ];
}
