// VTCLIP1 — bounded RGB565-RLE frame sequence.
//
// This is the on-device representation of the Live2D pipeline output: the
// character is rendered on a PC, exported as a frame sequence, cropped to the
// panel rectangle and packed here. The ESP32-S3 only ever decodes RLE spans
// straight into the RGB565 framebuffer; no Live2D runtime ships on device.
//
// Layout (little endian):
//   0  ..7   magic "VTCLIP1\0"
//   8  ..9   width       u16
//   10 ..11  height      u16
//   12 ..13  frame_count u16
//   14 ..15  fps         u16
//   16 ..19  flags       u32   (all bits reserved, must be 0)
//   20 ..23  payload_len u32
//   24 ..27  payload_crc u32   (CRC-32/ISO-HDLC over the payload)
//   28 ..31  reserved    u32   (must be 0)
//   32 ..    frame_count * u32 payload-relative frame offsets, monotonic
//   ...      payload
//
// Frame stream is PackBits over RGB565 pixels:
//   op & 0x80 -> run of (op & 0x7F) + 1 pixels, followed by one RGB565 pixel
//   op & 0x80 == 0 -> literal of op + 1 pixels, followed by that many pixels
// A frame must decode to exactly width * height pixels.

import { CLIP_FLAG_DELTA, CLIP_HEADER_BYTES, CLIP_KNOWN_FLAGS, CLIP_MAGIC, crc32 } from "./clip-codec.js";
import { PANEL } from "./contract.js";

export { CLIP_HEADER_BYTES, CLIP_MAGIC, crc32 };
// ui_0/ui_1 trong veetee-firmware/partitions/veetee_16mb.csv hiện là 0x200000.
// File đó tự ghi là "Provisional ... Freeze only after ESP-SR, Opus, TLS and UI
// size probes", nên đây là số đang chờ đo chứ chưa chốt. Demo báo cáo theo mốc
// hiện tại nhưng chỉ từ chối khi vượt cả mốc lớn nhất đang được cân nhắc.
export const UI_SLOT_BYTES = 2 * 1024 * 1024;
export const UI_SLOT_CEILING_BYTES = 4 * 1024 * 1024;

export function slotSizeFor(totalBytes) {
  for (const megabytes of [2, 3, 4]) {
    if (totalBytes <= megabytes * 1024 * 1024) return megabytes;
  }
  return null;
}

export class ClipFormatError extends Error {}

export function decodeClip(buffer, name = "clip") {
  const bytes = new Uint8Array(buffer);
  if (bytes.length < CLIP_HEADER_BYTES) throw new ClipFormatError(`${name}: quá ngắn`);
  for (let index = 0; index < 8; index += 1) {
    if (bytes[index] !== CLIP_MAGIC.charCodeAt(index)) throw new ClipFormatError(`${name}: sai magic VTCLIP1`);
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

  if ((flags & ~CLIP_KNOWN_FLAGS) !== 0 || reserved !== 0) {
    throw new ClipFormatError(`${name}: flags/reserved chứa bit chưa định nghĩa`);
  }
  if (width === 0 || height === 0 || frameCount === 0) throw new ClipFormatError(`${name}: kích thước rỗng`);
  if (fps === 0 || fps > 60) throw new ClipFormatError(`${name}: fps ngoài khoảng 1..60`);

  const indexBytes = frameCount * 4;
  const payloadStart = CLIP_HEADER_BYTES + indexBytes;
  if (bytes.length !== payloadStart + payloadLength) throw new ClipFormatError(`${name}: payload_len không khớp file`);

  const offsets = new Uint32Array(frameCount);
  let previous = -1;
  for (let index = 0; index < frameCount; index += 1) {
    const offset = view.getUint32(CLIP_HEADER_BYTES + index * 4, true);
    if (offset <= previous || offset > payloadLength) throw new ClipFormatError(`${name}: offset frame không hợp lệ`);
    offsets[index] = offset;
    previous = offset;
  }

  const payload = bytes.subarray(payloadStart);
  if (crc32(payload) !== payloadCrc) throw new ClipFormatError(`${name}: CRC32 payload sai`);

  return {
    name,
    width,
    height,
    fps,
    frameCount,
    delta: (flags & CLIP_FLAG_DELTA) !== 0,
    byteLength: bytes.length,
    offsets,
    payload,
    cache: new Array(frameCount).fill(null),
  };
}

export function clipFrame(clip, index) {
  const frameIndex = ((index % clip.frameCount) + clip.frameCount) % clip.frameCount;
  const cached = clip.cache[frameIndex];
  if (cached) return cached;
  if (!clip.delta) {
    clip.cache[frameIndex] = decodeKeyframe(clip, frameIndex);
    return clip.cache[frameIndex];
  }

  // Frame delta chỉ có nghĩa khi đặt lên frame trước, nên lùi về keyframe hoặc
  // frame đã cache gần nhất rồi dựng tiến lên. Trên thiết bị bước này miễn phí:
  // framebuffer đã chứa sẵn frame trước, delta chỉ vá lên đó.
  let start = frameIndex;
  while (start > 0 && !clip.cache[start - 1]) start -= 1;
  let previous = start === 0 ? null : clip.cache[start - 1];
  for (let step = start; step <= frameIndex; step += 1) {
    if (!clip.cache[step]) {
      clip.cache[step] = step === 0 ? decodeKeyframe(clip, 0) : decodeDelta(clip, step, previous);
    }
    previous = clip.cache[step];
  }
  return clip.cache[frameIndex];
}

function frameRange(clip, frameIndex) {
  return {
    read: clip.offsets[frameIndex],
    end: frameIndex + 1 < clip.frameCount ? clip.offsets[frameIndex + 1] : clip.payload.length,
  };
}

function writePixel(out, pixel, packed) {
  const [red, green, blue] = unpack565(packed);
  const target = pixel * 4;
  out[target] = red;
  out[target + 1] = green;
  out[target + 2] = blue;
  out[target + 3] = 255;
}

function decodeKeyframe(clip, frameIndex) {
  const { end } = frameRange(clip, frameIndex);
  let { read } = frameRange(clip, frameIndex);
  const image = new ImageData(clip.width, clip.height);
  const out = image.data;
  const total = clip.width * clip.height;
  let pixel = 0;

  while (pixel < total) {
    if (read >= end) throw new ClipFormatError(`${clip.name}: frame ${frameIndex} thiếu dữ liệu`);
    const op = clip.payload[read];
    read += 1;
    if ((op & 0x80) !== 0) {
      const count = (op & 0x7f) + 1;
      if (read + 2 > end || pixel + count > total) throw new ClipFormatError(`${clip.name}: run vượt biên`);
      const packed = clip.payload[read] | (clip.payload[read + 1] << 8);
      read += 2;
      for (let step = 0; step < count; step += 1) writePixel(out, pixel + step, packed);
      pixel += count;
    } else {
      const count = op + 1;
      if (read + count * 2 > end || pixel + count > total) throw new ClipFormatError(`${clip.name}: literal vượt biên`);
      for (let step = 0; step < count; step += 1) {
        writePixel(out, pixel + step, clip.payload[read] | (clip.payload[read + 1] << 8));
        read += 2;
      }
      pixel += count;
    }
  }
  return image;
}

function decodeDelta(clip, frameIndex, previous) {
  if (!previous) throw new ClipFormatError(`${clip.name}: frame ${frameIndex} delta nhưng thiếu frame trước`);
  const { end } = frameRange(clip, frameIndex);
  let { read } = frameRange(clip, frameIndex);
  const image = new ImageData(clip.width, clip.height);
  image.data.set(previous.data);
  const out = image.data;
  const total = clip.width * clip.height;
  let pixel = 0;

  while (pixel < total) {
    if (read >= end) throw new ClipFormatError(`${clip.name}: frame ${frameIndex} thiếu dữ liệu`);
    const op = clip.payload[read];
    read += 1;
    if (op === 0xff) {
      if (read + 2 > end) throw new ClipFormatError(`${clip.name}: skip16 vượt biên`);
      const count = clip.payload[read] | (clip.payload[read + 1] << 8);
      read += 2;
      if (count === 0 || pixel + count > total) throw new ClipFormatError(`${clip.name}: skip16 vượt biên`);
      pixel += count;
    } else if (op >= 0xc0) {
      const count = (op & 0x3f) + 1;
      if (pixel + count > total) throw new ClipFormatError(`${clip.name}: skip vượt biên`);
      pixel += count;
    } else if ((op & 0x80) !== 0) {
      const count = (op & 0x3f) + 1;
      if (read + 2 > end || pixel + count > total) throw new ClipFormatError(`${clip.name}: run vượt biên`);
      const packed = clip.payload[read] | (clip.payload[read + 1] << 8);
      read += 2;
      for (let step = 0; step < count; step += 1) writePixel(out, pixel + step, packed);
      pixel += count;
    } else {
      const count = op + 1;
      if (read + count * 2 > end || pixel + count > total) throw new ClipFormatError(`${clip.name}: literal vượt biên`);
      for (let step = 0; step < count; step += 1) {
        writePixel(out, pixel + step, clip.payload[read] | (clip.payload[read + 1] << 8));
        read += 2;
      }
      pixel += count;
    }
  }
  return image;
}

export async function loadClipSet(baseUrl) {
  const manifestUrl = new URL("manifest.json", baseUrl);
  const response = await fetch(manifestUrl, { cache: "no-store" });
  if (response.status === 404) {
    throw new Error(
      "chưa có assets/hiyori/manifest.json — xuất clip bằng tools/capture-hiyori.html, " +
        "hoặc đổi tên manifest.example.json sau khi đóng gói bằng tools/pack-clip.mjs",
    );
  }
  if (!response.ok) throw new Error(`Không đọc được ${manifestUrl.pathname} (${response.status})`);
  const manifest = await response.json();
  return hydrateClipSet(manifest, async (file) => {
    const fileResponse = await fetch(new URL(file, baseUrl), { cache: "no-store" });
    if (!fileResponse.ok) throw new Error(`Thiếu ${file} (${fileResponse.status})`);
    return fileResponse.arrayBuffer();
  });
}

export async function hydrateClipSet(manifest, readFile) {
  if (manifest?.kind !== "ui_clip_set") throw new ClipFormatError("manifest.json: kind phải là ui_clip_set");
  const clips = {};
  const overlays = {};
  let totalBytes = 0;

  const load = async (target, id, entry) => {
    const clip = decodeClip(await readFile(entry.file), entry.file);
    assertFitsPanel(entry, clip);
    target[id] = { ...entry, clip };
    totalBytes += clip.byteLength;
  };

  for (const [id, entry] of Object.entries(manifest.clips ?? {})) await load(clips, id, entry);
  for (const [id, entry] of Object.entries(manifest.overlays ?? {})) await load(overlays, id, entry);

  // Overlay per-frame phải có đúng baseFrames * levels frame, nếu không thì
  // runtime sẽ tra nhầm và dán miệng của pose khác lên mặt.
  for (const [id, entry] of Object.entries(overlays)) {
    if (!entry.per_frame) continue;
    const base = clips[entry.base ?? "speaking"];
    if (!base) throw new ClipFormatError(`overlay ${id}: thiếu clip nền "${entry.base ?? "speaking"}"`);
    const expected = base.clip.frameCount * (entry.levels ?? 1);
    if (entry.clip.frameCount !== expected) {
      throw new ClipFormatError(
        `overlay ${id}: có ${entry.clip.frameCount} frame nhưng clip nền "${entry.base}" cần ` +
          `${base.clip.frameCount} × ${entry.levels} = ${expected}`,
      );
    }
  }

  // Giới hạn panel là ràng buộc cứng nên chặn thẳng; còn dung lượng slot thì
  // chưa chốt, nên chỉ chặn khi vượt cả mốc lớn nhất đang cân nhắc.
  if (totalBytes > UI_SLOT_CEILING_BYTES) {
    throw new ClipFormatError(
      `bộ clip ${(totalBytes / 1024 / 1024).toFixed(2)} MiB vượt cả mốc 4 MiB lớn nhất đang cân nhắc ` +
        `cho ui_0/ui_1 — giảm số frame hoặc fps`,
    );
  }
  return { manifest, clips, overlays, totalBytes };
}

function assertFitsPanel(entry, clip) {
  const x = entry.x ?? 0;
  const y = entry.y ?? 0;
  if (x < 0 || y < 0 || x + clip.width > PANEL.width || y + clip.height > PANEL.height) {
    throw new ClipFormatError(
      `${entry.file}: ${clip.width}x${clip.height} tại (${x}, ${y}) nằm ngoài panel ${PANEL.width}x${PANEL.height}`,
    );
  }
}

export function clipForState(set, state) {
  const clipId = set.manifest.state_map?.[state] ?? "idle";
  return set.clips[clipId] ?? set.clips.idle ?? null;
}

export function unpack565(packed) {
  const red = (packed >> 11) & 0x1f;
  const green = (packed >> 5) & 0x3f;
  const blue = packed & 0x1f;
  return [(red * 255 + 15) / 31 | 0, (green * 255 + 31) / 63 | 0, (blue * 255 + 15) / 31 | 0];
}

