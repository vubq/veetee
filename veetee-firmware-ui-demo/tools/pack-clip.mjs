#!/usr/bin/env node
// PNG sequence -> VTCLIP1 (RGB565 + PackBits RLE).
//
// This is the Cubism Editor route of the pipeline:
//   Hiyori.cmo3 -> Cubism Editor -> PNG sequence -> pack-clip.mjs -> .vclip
//
// Dependency-free on purpose: PNG inflate uses node:zlib, and the resampler is
// a box filter, so the demo stays build-free and reviewable.
//
// Usage:
//   node tools/pack-clip.mjs pack <frames-dir> <out.vclip> [options]
//   node tools/pack-clip.mjs inspect <file.vclip>
//
// Options:
//   --fps=12               playback rate baked into the header (1..60)
//   --width=240            target width
//   --height=280           target height
//   --fit=cover|contain    how to map the source frame onto the panel
//   --background=#102C33   opaque colour composited under PNG alpha
//   --limit=48             stop after N frames

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { inflateSync } from "node:zlib";

import {
  CLIP_FLAG_DELTA,
  CLIP_HEADER_BYTES as HEADER_BYTES,
  CLIP_MAGIC,
  crc32,
  encodeFrameSequence,
  pack565,
  writeClip,
} from "../src/clip-codec.js";

const UI_SLOT_BYTES = 2 * 1024 * 1024;

main(process.argv.slice(2));

function main(argv) {
  const [command, ...rest] = argv;
  const positional = rest.filter((value) => !value.startsWith("--"));
  const options = Object.fromEntries(
    rest
      .filter((value) => value.startsWith("--"))
      .map((value) => {
        const [key, raw = "true"] = value.slice(2).split("=");
        return [key, raw];
      }),
  );

  if (command === "pack" && positional.length === 2) return pack(positional[0], positional[1], options);
  if (command === "inspect" && positional.length === 1) return inspect(positional[0]);
  process.stderr.write(
    "Usage:\n" +
      "  node tools/pack-clip.mjs pack <frames-dir> <out.vclip> [--fps=12] [--width=240] [--height=280] [--fit=cover] [--background=#102C33] [--limit=N]\n" +
      "  node tools/pack-clip.mjs inspect <file.vclip>\n",
  );
  process.exitCode = 2;
}

function pack(directory, outputPath, options) {
  const width = Number(options.width ?? 240);
  const height = Number(options.height ?? 280);
  const fps = Number(options.fps ?? 12);
  const fit = options.fit ?? "cover";
  const background = parseHex(options.background ?? "#102C33");
  const limit = options.limit ? Number(options.limit) : Infinity;

  if (!Number.isInteger(fps) || fps < 1 || fps > 60) throw new Error("--fps phải nằm trong 1..60");
  if (!["cover", "contain", "none"].includes(fit)) throw new Error("--fit phải là cover, contain hoặc none");

  const files = readdirSync(directory)
    .filter((name) => name.toLowerCase().endsWith(".png"))
    .sort(naturalOrder)
    .slice(0, limit);
  if (files.length === 0) throw new Error(`Không tìm thấy PNG trong ${directory}`);
  if (files.length > 65535) throw new Error("Vượt quá 65535 frame");

  const framePixels = [];
  for (const name of files) {
    const png = decodePng(readFileSync(join(directory, name)));
    framePixels.push(toPanelPixels(png, width, height, fit, background));
  }

  const buffer = writeClip(encodeFrameSequence(framePixels), {
    width,
    height,
    fps,
    flags: framePixels.length > 1 ? CLIP_FLAG_DELTA : 0,
  });
  writeFileSync(outputPath, buffer);

  const rawBytes = width * height * 2 * files.length;
  process.stdout.write(
    `${basename(outputPath)}: ${files.length} frame · ${width}x${height} · ${fps} fps\n` +
      `  ${formatBytes(buffer.length)} (raw ${formatBytes(rawBytes)}, tỉ lệ ${(rawBytes / buffer.length).toFixed(1)}x)\n` +
      `  ${((buffer.length / UI_SLOT_BYTES) * 100).toFixed(1)}% của một UI slot 2 MiB\n`,
  );
}

function inspect(path) {
  const bytes = readFileSync(path);
  for (let index = 0; index < 8; index += 1) {
    if (bytes[index] !== CLIP_MAGIC.charCodeAt(index)) throw new Error("Sai magic VTCLIP1");
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint16(8, true);
  const height = view.getUint16(10, true);
  const frameCount = view.getUint16(12, true);
  const fps = view.getUint16(14, true);
  const flags = view.getUint32(16, true);
  const payloadLength = view.getUint32(20, true);
  const payloadCrc = view.getUint32(24, true);
  const reserved = view.getUint32(28, true);

  const payloadStart = HEADER_BYTES + frameCount * 4;
  const problems = [];
  if ((flags & ~CLIP_FLAG_DELTA) !== 0 || reserved !== 0) problems.push("flags/reserved chứa bit chưa định nghĩa");
  const encoding = (flags & CLIP_FLAG_DELTA) !== 0 ? "keyframe + delta" : "keyframe độc lập";
  if (bytes.length !== payloadStart + payloadLength) problems.push("payload_len không khớp kích thước file");
  const actualCrc = crc32(bytes.subarray(payloadStart));
  if (actualCrc !== payloadCrc) problems.push(`CRC32 sai (header ${payloadCrc}, thực tế ${actualCrc})`);

  let previous = -1;
  for (let index = 0; index < frameCount; index += 1) {
    const offset = view.getUint32(HEADER_BYTES + index * 4, true);
    if (offset <= previous || offset > payloadLength) problems.push(`offset frame ${index} không hợp lệ`);
    previous = offset;
  }

  const duration = frameCount / fps;
  process.stdout.write(
    `${basename(path)}\n` +
      `  ${width}x${height} · ${frameCount} frame · ${fps} fps · ${duration.toFixed(2)} s · ${encoding}\n` +
      `  ${formatBytes(bytes.length)} · ${((bytes.length / UI_SLOT_BYTES) * 100).toFixed(1)}% slot 2 MiB\n` +
      `  trung bình ${formatBytes(payloadLength / frameCount)}/frame\n` +
      (problems.length === 0 ? "  OK\n" : problems.map((problem) => `  LỖI: ${problem}\n`).join("")),
  );
  if (problems.length > 0) process.exitCode = 1;
}

// ------------------------------------------------------------ pixel stage

function toPanelPixels(png, width, height, fit, background) {
  if (fit === "none" && (png.width !== width || png.height !== height)) {
    throw new Error(`--fit=none yêu cầu PNG đúng ${width}x${height}, nhận ${png.width}x${png.height}`);
  }
  const scale =
    fit === "contain"
      ? Math.min(width / png.width, height / png.height)
      : Math.max(width / png.width, height / png.height);
  const drawWidth = fit === "none" ? width : png.width * scale;
  const drawHeight = fit === "none" ? height : png.height * scale;
  const offsetX = (width - drawWidth) / 2;
  const offsetY = (height - drawHeight) / 2;

  const pixels = new Uint16Array(width * height);
  const backgroundPacked = pack565(background[0], background[1], background[2]);
  pixels.fill(backgroundPacked);

  for (let y = 0; y < height; y += 1) {
    // Source rectangle covered by this destination row.
    const sourceTop = ((y - offsetY) / drawHeight) * png.height;
    const sourceBottom = ((y + 1 - offsetY) / drawHeight) * png.height;
    for (let x = 0; x < width; x += 1) {
      const sourceLeft = ((x - offsetX) / drawWidth) * png.width;
      const sourceRight = ((x + 1 - offsetX) / drawWidth) * png.width;
      const sample = boxAverage(png, sourceLeft, sourceTop, sourceRight, sourceBottom);
      if (!sample) continue;
      const alpha = sample[3] / 255;
      const red = Math.round(sample[0] * alpha + background[0] * (1 - alpha));
      const green = Math.round(sample[1] * alpha + background[1] * (1 - alpha));
      const blue = Math.round(sample[2] * alpha + background[2] * (1 - alpha));
      pixels[y * width + x] = pack565(red, green, blue);
    }
  }
  return pixels;
}

function boxAverage(png, left, top, right, bottom) {
  const startX = Math.max(0, Math.floor(left));
  const startY = Math.max(0, Math.floor(top));
  const endX = Math.min(png.width, Math.max(startX + 1, Math.ceil(right)));
  const endY = Math.min(png.height, Math.max(startY + 1, Math.ceil(bottom)));
  if (startX >= png.width || startY >= png.height || endX <= 0 || endY <= 0) return null;

  let red = 0;
  let green = 0;
  let blue = 0;
  let alpha = 0;
  let count = 0;
  for (let y = startY; y < endY; y += 1) {
    for (let x = startX; x < endX; x += 1) {
      const index = (y * png.width + x) * 4;
      const weight = png.data[index + 3] / 255;
      red += png.data[index] * weight;
      green += png.data[index + 1] * weight;
      blue += png.data[index + 2] * weight;
      alpha += png.data[index + 3];
      count += 1;
    }
  }
  if (count === 0) return null;
  // Colour is averaged premultiplied, then unpremultiplied by the alpha mass,
  // so transparent edge pixels do not drag the character toward black.
  const weightSum = alpha / 255 || 1;
  return [red / weightSum, green / weightSum, blue / weightSum, alpha / count];
}

function parseHex(hex) {
  const value = Number.parseInt(hex.replace("#", ""), 16);
  if (Number.isNaN(value)) throw new Error(`Màu không hợp lệ: ${hex}`);
  return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
}

// -------------------------------------------------------------- PNG input

function decodePng(buffer) {
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  for (let index = 0; index < signature.length; index += 1) {
    if (buffer[index] !== signature[index]) throw new Error("Không phải file PNG");
  }

  let cursor = 8;
  let header = null;
  const idat = [];
  while (cursor < buffer.length) {
    const length = buffer.readUInt32BE(cursor);
    const type = buffer.toString("latin1", cursor + 4, cursor + 8);
    const data = buffer.subarray(cursor + 8, cursor + 8 + length);
    if (type === "IHDR") {
      header = {
        width: data.readUInt32BE(0),
        height: data.readUInt32BE(4),
        bitDepth: data[8],
        colorType: data[9],
        interlace: data[12],
      };
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      break;
    }
    cursor += 12 + length;
  }

  if (!header) throw new Error("PNG thiếu IHDR");
  if (header.bitDepth !== 8) throw new Error("Chỉ hỗ trợ PNG 8-bit; hãy xuất lại ở 8 bit/kênh");
  if (header.interlace !== 0) throw new Error("Không hỗ trợ PNG interlaced (Adam7)");
  const channels = { 0: 1, 2: 3, 6: 4 }[header.colorType];
  if (!channels) throw new Error(`Không hỗ trợ colour type ${header.colorType}; dùng grayscale, RGB hoặc RGBA`);

  const raw = inflateSync(Buffer.concat(idat));
  const stride = header.width * channels;
  const lines = new Uint8Array(header.height * stride);

  for (let y = 0; y < header.height; y += 1) {
    const filter = raw[y * (stride + 1)];
    const source = raw.subarray(y * (stride + 1) + 1, y * (stride + 1) + 1 + stride);
    const target = lines.subarray(y * stride, (y + 1) * stride);
    const previous = y > 0 ? lines.subarray((y - 1) * stride, y * stride) : null;
    unfilter(filter, source, target, previous, channels);
  }

  const data = new Uint8Array(header.width * header.height * 4);
  for (let index = 0; index < header.width * header.height; index += 1) {
    const source = index * channels;
    const target = index * 4;
    if (channels === 1) {
      data[target] = lines[source];
      data[target + 1] = lines[source];
      data[target + 2] = lines[source];
      data[target + 3] = 255;
    } else {
      data[target] = lines[source];
      data[target + 1] = lines[source + 1];
      data[target + 2] = lines[source + 2];
      data[target + 3] = channels === 4 ? lines[source + 3] : 255;
    }
  }
  return { width: header.width, height: header.height, data };
}

function unfilter(filter, source, target, previous, bpp) {
  for (let index = 0; index < source.length; index += 1) {
    const left = index >= bpp ? target[index - bpp] : 0;
    const up = previous ? previous[index] : 0;
    const upLeft = previous && index >= bpp ? previous[index - bpp] : 0;
    let value = source[index];
    switch (filter) {
      case 0:
        break;
      case 1:
        value += left;
        break;
      case 2:
        value += up;
        break;
      case 3:
        value += (left + up) >> 1;
        break;
      case 4:
        value += paeth(left, up, upLeft);
        break;
      default:
        throw new Error(`Filter PNG không hợp lệ: ${filter}`);
    }
    target[index] = value & 0xff;
  }
}

function paeth(left, up, upLeft) {
  const estimate = left + up - upLeft;
  const distanceLeft = Math.abs(estimate - left);
  const distanceUp = Math.abs(estimate - up);
  const distanceUpLeft = Math.abs(estimate - upLeft);
  if (distanceLeft <= distanceUp && distanceLeft <= distanceUpLeft) return left;
  return distanceUp <= distanceUpLeft ? up : upLeft;
}

// ------------------------------------------------------------------ utils

function naturalOrder(left, right) {
  return left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" });
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}
