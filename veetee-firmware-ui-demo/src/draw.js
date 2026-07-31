// Primitive set the firmware renderer has to be able to reproduce.
//
// Every helper here maps to one analytic-coverage span filler on device:
//   fillRound / strokeRound -> rounded-rect distance field, 1 span per row
//   fillCircle / strokeArc  -> circle distance field, 1..2 spans per row
//   capsule                 -> segment distance field, 1 span per row
//   text                    -> 8-bit alpha glyph blit from a .vfont atlas
//
// Nothing here uses shadows, blurs, image filters or per-pixel compositing the
// ESP32-S3 cannot afford, and no gradient is used outside `scrim`, which is a
// single precomputed per-row alpha ramp.

export const UI_FONT =
  '"Be Vietnam Pro", "Inter", "Segoe UI Variable Text", "Segoe UI", system-ui, "Helvetica Neue", Arial, sans-serif';

export function roundedPath(ctx, x, y, width, height, radius) {
  const limit = Math.min(width, height) / 2;
  const r = Math.max(0, Math.min(radius, limit));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.arcTo(x + width, y, x + width, y + r, r);
  ctx.lineTo(x + width, y + height - r);
  ctx.arcTo(x + width, y + height, x + width - r, y + height, r);
  ctx.lineTo(x + r, y + height);
  ctx.arcTo(x, y + height, x, y + height - r, r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

export function fillRound(ctx, x, y, width, height, radius, color) {
  if (width <= 0 || height <= 0) return;
  roundedPath(ctx, x, y, width, height, radius);
  ctx.fillStyle = color;
  ctx.fill();
}

export function strokeRound(ctx, x, y, width, height, radius, color, lineWidth = 1) {
  if (width <= 0 || height <= 0) return;
  const inset = lineWidth / 2;
  roundedPath(ctx, x + inset, y + inset, width - lineWidth, height - lineWidth, radius - inset);
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.stroke();
}

export function fillCircle(ctx, centerX, centerY, radius, color) {
  if (radius <= 0) return;
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

export function strokeArc(ctx, centerX, centerY, radius, startAngle, endAngle, color, lineWidth = 2, cap = "round") {
  if (radius <= 0) return;
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, startAngle, endAngle);
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.lineCap = cap;
  ctx.stroke();
  ctx.lineCap = "butt";
}

export function capsule(ctx, x0, y0, x1, y1, thickness, color) {
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.strokeStyle = color;
  ctx.lineWidth = thickness;
  ctx.lineCap = "round";
  ctx.stroke();
  ctx.lineCap = "butt";
}

// Single vertical alpha ramp; on device this is one precomputed 0..255 table
// blended per row, not a shader.
export function scrim(ctx, x, y, width, height, color, fromAlpha, toAlpha) {
  const gradient = ctx.createLinearGradient(0, y, 0, y + height);
  gradient.addColorStop(0, withAlpha(color, fromAlpha));
  gradient.addColorStop(1, withAlpha(color, toAlpha));
  ctx.fillStyle = gradient;
  ctx.fillRect(x, y, width, height);
}

export function text(ctx, value, x, y, options = {}) {
  const {
    size = 12,
    weight = 500,
    color = "#ffffff",
    tracking = 0,
    align = "left",
    baseline = "alphabetic",
    maxWidth = 0,
    uppercase = false,
  } = options;
  const content = uppercase ? String(value).toUpperCase() : String(value);
  if (!content) return 0;

  let fontSize = size;
  ctx.font = `${weight} ${fontSize}px ${UI_FONT}`;
  let width = measure(ctx, content, tracking);
  while (maxWidth > 0 && width > maxWidth && fontSize > 8) {
    fontSize -= 0.5;
    ctx.font = `${weight} ${fontSize}px ${UI_FONT}`;
    width = measure(ctx, content, tracking);
  }

  ctx.fillStyle = color;
  ctx.textBaseline = baseline;
  ctx.textAlign = "left";
  let cursor = x;
  if (align === "center") cursor = x - width / 2;
  if (align === "right") cursor = x - width;

  if (tracking === 0) {
    ctx.fillText(content, cursor, y);
    return width;
  }
  for (const character of content) {
    ctx.fillText(character, cursor, y);
    cursor += ctx.measureText(character).width + tracking;
  }
  return width;
}

// Chữ lớn thì phải biết xuống dòng, nếu không tiêu đề tiếng Việt có dấu sẽ tự
// co lại thành chữ nhỏ — đúng thứ làm hỏng phân cấp. Ưu tiên giữ cỡ chữ và
// ngắt dòng; chỉ co khi ngắt dòng vẫn không đủ chỗ.
export function textBlock(ctx, value, x, y, options = {}) {
  const {
    size = 32,
    weight = 650,
    color = "#ffffff",
    maxWidth = 200,
    maxLines = 2,
    lineHeight = 1.1,
    minSize = 15,
  } = options;

  let fontSize = size;
  let lines = wrapLines(ctx, value, fontSize, weight, maxWidth);
  while (lines.length > maxLines && fontSize > minSize) {
    fontSize -= 1;
    lines = wrapLines(ctx, value, fontSize, weight, maxWidth);
  }
  lines = lines.slice(0, maxLines);

  ctx.fillStyle = color;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.font = `${weight} ${fontSize}px ${UI_FONT}`;
  lines.forEach((line, index) => ctx.fillText(line, x, y + index * fontSize * lineHeight));
  return { lines: lines.length, fontSize, height: lines.length * fontSize * lineHeight };
}

function wrapLines(ctx, value, size, weight, maxWidth) {
  ctx.font = `${weight} ${size}px ${UI_FONT}`;
  const words = String(value).split(/\s+/).filter(Boolean);
  const lines = [];
  let current = "";
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (!current || ctx.measureText(candidate).width <= maxWidth) {
      current = candidate;
    } else {
      lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  return lines.length > 0 ? lines : [""];
}

export function measure(ctx, content, tracking = 0) {
  const base = ctx.measureText(content).width;
  return tracking === 0 ? base : base + tracking * Math.max(0, [...content].length - 1);
}

export function measureText(ctx, value, { size = 12, weight = 500, tracking = 0, uppercase = false } = {}) {
  const content = uppercase ? String(value).toUpperCase() : String(value);
  ctx.font = `${weight} ${size}px ${UI_FONT}`;
  return measure(ctx, content, tracking);
}

// Veetee mark: rounded square, slight tilt, two light dots. Same geometry as
// Manager Web; geometry is one of the things the interface language shares
// literally across runtimes.
export function veeMark(ctx, x, y, size, bodyColor, dotColor) {
  ctx.save();
  ctx.translate(x + size / 2, y + size / 2);
  ctx.rotate((-12 * Math.PI) / 180);
  fillRound(ctx, -size / 2, -size / 2, size, size, size * 0.32, bodyColor);
  const dot = Math.max(1, size * 0.13);
  fillCircle(ctx, -size * 0.17, -size * 0.06, dot, dotColor);
  fillCircle(ctx, size * 0.19, size * 0.02, dot, dotColor);
  ctx.restore();
}

export function wifiIcon(ctx, x, y, color, bars = 3) {
  const levels = [3.5, 6.5, 9.5];
  for (let index = 0; index < levels.length; index += 1) {
    strokeArc(
      ctx,
      x,
      y,
      levels[index],
      (-140 * Math.PI) / 180,
      (-40 * Math.PI) / 180,
      index < bars ? color : withAlpha(color, 0.25),
      1.6,
    );
  }
  fillCircle(ctx, x, y - 0.5, 1.3, bars > 0 ? color : withAlpha(color, 0.25));
}

export function batteryIcon(ctx, x, y, width, height, color, fraction) {
  strokeRound(ctx, x, y, width, height, 3, color, 1.2);
  fillRound(ctx, x + width + 1, y + height / 2 - 2, 1.8, 4, 1, color);
  const inner = Math.max(0, (width - 4) * Math.max(0, Math.min(1, fraction)));
  fillRound(ctx, x + 2, y + 2, inner, height - 4, 1.5, color);
}

export function micIcon(ctx, x, y, color, active) {
  fillRound(ctx, x - 2.5, y - 7, 5, 9, 2.5, color);
  strokeArc(ctx, x, y, 5.5, 0, Math.PI, color, 1.4);
  capsule(ctx, x, y + 5.5, x, y + 8, 1.4, color);
  if (active) {
    strokeArc(ctx, x, y - 3, 9, (-60 * Math.PI) / 180, (60 * Math.PI) / 180, withAlpha(color, 0.5), 1.2);
  }
}

export function withAlpha(color, alpha) {
  if (color.startsWith("#")) {
    const value = Number.parseInt(color.slice(1), 16);
    return `rgba(${(value >> 16) & 0xff}, ${(value >> 8) & 0xff}, ${value & 0xff}, ${alpha})`;
  }
  const parts = color.match(/[\d.]+/g);
  return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
}

export function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

export function easeInOut(value) {
  const t = clamp(value, 0, 1);
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}
