// VTCLIP1 writer shared by the browser capture tool and tools/pack-clip.mjs.
// Plain ESM with no DOM or Node API so both runtimes can import it directly.
// See src/clip.js for the container layout and the reader.

export const CLIP_MAGIC = "VTCLIP1\0";
export const CLIP_HEADER_BYTES = 32;

// flags bit 0: frame 0 là keyframe, các frame sau mã hoá delta so với frame
// trước. Các bit còn lại vẫn phải bằng 0.
export const CLIP_FLAG_DELTA = 1;
export const CLIP_KNOWN_FLAGS = CLIP_FLAG_DELTA;

// PackBits over RGB565 pixels. A run costs 3 bytes, so leaving a literal only
// pays off from three identical pixels upward.
export function encodeFrame(pixels) {
  const out = [];
  let literal = [];

  const flushLiteral = () => {
    let start = 0;
    while (start < literal.length) {
      const take = Math.min(128, literal.length - start);
      out.push(take - 1);
      for (let index = 0; index < take; index += 1) {
        const pixel = literal[start + index];
        out.push(pixel & 0xff, (pixel >> 8) & 0xff);
      }
      start += take;
    }
    literal = [];
  };

  let cursor = 0;
  while (cursor < pixels.length) {
    let run = 1;
    while (cursor + run < pixels.length && pixels[cursor + run] === pixels[cursor] && run < 128) run += 1;
    if (run >= 3) {
      flushLiteral();
      out.push(0x80 | (run - 1), pixels[cursor] & 0xff, (pixels[cursor] >> 8) & 0xff);
      cursor += run;
    } else {
      literal.push(pixels[cursor]);
      cursor += 1;
      if (literal.length === 128) flushLiteral();
    }
  }
  flushLiteral();
  return Uint8Array.from(out);
}

// Delta so với frame trước. Đây mới là chỗ nén thật của nội dung nhân vật:
// RLE trong một frame chỉ ăn được vùng phẳng, còn giữa hai frame liên tiếp thì
// đại đa số pixel giống hệt nhau nên SKIP nuốt gọn.
//
// Op byte:
//   0x00..0x7F  LITERAL, count = op + 1        (1..128), theo sau count pixel
//   0x80..0xBF  RUN,     count = (op&0x3F) + 1 (1..64),  theo sau 1 pixel
//   0xC0..0xFE  SKIP,    count = (op&0x3F) + 1 (1..63)
//   0xFF        SKIP16,  theo sau u16 LE count (1..65535)
export function encodeDeltaFrame(pixels, previous) {
  const out = [];
  let literal = [];

  const flushLiteral = () => {
    let start = 0;
    while (start < literal.length) {
      const take = Math.min(128, literal.length - start);
      out.push(take - 1);
      for (let index = 0; index < take; index += 1) {
        const pixel = literal[start + index];
        out.push(pixel & 0xff, (pixel >> 8) & 0xff);
      }
      start += take;
    }
    literal = [];
  };

  const emitSkip = (count) => {
    let left = count;
    while (left > 0) {
      if (left <= 63) {
        out.push(0xc0 + left - 1);
        left = 0;
      } else {
        const take = Math.min(left, 65535);
        out.push(0xff, take & 0xff, (take >> 8) & 0xff);
        left -= take;
      }
    }
  };

  let cursor = 0;
  while (cursor < pixels.length) {
    let same = 0;
    while (cursor + same < pixels.length && pixels[cursor + same] === previous[cursor + same]) same += 1;
    // Bỏ qua 1 pixel tốn đúng bằng ghi thẳng nó, nên chỉ cắt literal từ 2 trở lên.
    if (same >= 2) {
      flushLiteral();
      emitSkip(same);
      cursor += same;
      continue;
    }
    let run = 1;
    while (cursor + run < pixels.length && pixels[cursor + run] === pixels[cursor] && run < 64) run += 1;
    if (run >= 3) {
      flushLiteral();
      out.push(0x80 + run - 1, pixels[cursor] & 0xff, (pixels[cursor] >> 8) & 0xff);
      cursor += run;
      continue;
    }
    literal.push(pixels[cursor]);
    cursor += 1;
    if (literal.length === 128) flushLiteral();
  }
  flushLiteral();
  return Uint8Array.from(out);
}

// Frame 0 mã hoá độc lập để đổi state là phát được ngay; các frame sau delta.
export function encodeFrameSequence(framePixels) {
  return framePixels.map((pixels, index) =>
    index === 0 ? encodeFrame(pixels) : encodeDeltaFrame(pixels, framePixels[index - 1]),
  );
}

export function writeClip(frames, { width, height, fps, flags = 0 }) {
  if (frames.length === 0 || frames.length > 65535) throw new Error("frame_count phải nằm trong 1..65535");
  if (fps < 1 || fps > 60) throw new Error("fps phải nằm trong 1..60");
  if ((flags & ~CLIP_KNOWN_FLAGS) !== 0) throw new Error("flags chứa bit chưa định nghĩa");

  const payloadLength = frames.reduce((total, frame) => total + frame.length, 0);
  const indexBytes = frames.length * 4;
  const buffer = new Uint8Array(CLIP_HEADER_BYTES + indexBytes + payloadLength);
  const view = new DataView(buffer.buffer);

  for (let index = 0; index < 8; index += 1) buffer[index] = CLIP_MAGIC.charCodeAt(index);
  view.setUint16(8, width, true);
  view.setUint16(10, height, true);
  view.setUint16(12, frames.length, true);
  view.setUint16(14, fps, true);
  view.setUint32(16, flags, true);
  view.setUint32(20, payloadLength, true);
  view.setUint32(28, 0, true);

  const payloadStart = CLIP_HEADER_BYTES + indexBytes;
  let offset = 0;
  frames.forEach((frame, index) => {
    view.setUint32(CLIP_HEADER_BYTES + index * 4, offset, true);
    buffer.set(frame, payloadStart + offset);
    offset += frame.length;
  });
  view.setUint32(24, crc32(buffer.subarray(payloadStart)), true);
  return buffer;
}

export function pack565(red, green, blue) {
  const clamp = (value) => Math.max(0, Math.min(255, Math.round(value)));
  return ((clamp(red) >> 3) << 11) | ((clamp(green) >> 2) << 5) | (clamp(blue) >> 3);
}

// Composites straight-alpha RGBA over an opaque background and quantizes to
// RGB565, which is exactly what the panel stores.
export function rgbaToPanelPixels(rgba, pixelCount, background) {
  const pixels = new Uint16Array(pixelCount);
  for (let index = 0; index < pixelCount; index += 1) {
    const source = index * 4;
    const alpha = rgba[source + 3] / 255;
    pixels[index] = pack565(
      rgba[source] * alpha + background[0] * (1 - alpha),
      rgba[source + 1] * alpha + background[1] * (1 - alpha),
      rgba[source + 2] * alpha + background[2] * (1 - alpha),
    );
  }
  return pixels;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? (value >>> 1) ^ 0xedb88320 : value >>> 1;
    table[index] = value >>> 0;
  }
  return table;
})();

export function crc32(bytes) {
  let crc = 0xffffffff;
  for (let index = 0; index < bytes.length; index += 1) crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ bytes[index]) & 0xff];
  return (crc ^ 0xffffffff) >>> 0;
}
