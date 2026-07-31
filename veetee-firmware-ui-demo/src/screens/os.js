// Composition `signal` — product name Mobile, demo name "OS".
//
// Hướng: tối giản. Tách khối bằng khoảng trắng, không bằng nét kẻ — ở RGB565 thì
// một nét 1px tương phản thấp gần như biến mất, nên viền vừa tốn vừa bẩn. Cả màn
// chỉ còn bốn thứ: định danh, trạng thái, tín hiệu, hành động tiếp theo.
//
// Dấu riêng của Veetee nằm ở hai chỗ, không phải ở trang trí thêm:
//   1. Mô-típ hai chấm lệch chéo của dấu nhận diện, lặp lại trước dòng kicker.
//   2. Thanh tín hiệu là dãy hình vuông bo góc theo đúng tỉ lệ bo của dấu, không
//      phải cột thẳng như mọi equalizer khác.

import { clamp, fillCircle, fillRound, text, textBlock, veeMark, withAlpha } from "../draw.js";

const MARGIN = 20;
const CONTENT = 200;
const BAND = { y: 196, height: 24 };

const METER_STATES = new Set(["idle", "listening", "speaking", "closing"]);

// Tỉ lệ bo góc của dấu nhận diện Veetee. Dùng lại đúng con số này cho thanh tín
// hiệu là cách nhắc thương hiệu mà không cần dán thêm logo.
const MARK_RADIUS_RATIO = 0.32;

export function renderOs(ctx, frame) {
  const { tokens, copy, state } = frame;

  veeMark(ctx, MARGIN, 13, 15, tokens.accent, tokens.background);
  text(ctx, copy.number, MARGIN + CONTENT, 26, {
    size: 11,
    weight: 600,
    color: tokens.muted,
    tracking: 1.2,
    align: "right",
  });

  drawSignatureDots(ctx, frame);
  text(ctx, copy.kicker, MARGIN + 18, 79, {
    size: 11,
    weight: 700,
    color: tokens.accent,
    tracking: 1.5,
    uppercase: true,
    maxWidth: CONTENT - 18,
  });

  if (state === "activating") {
    text(ctx, String(frame.activationCode ?? "284716"), MARGIN, 134, {
      size: 38,
      weight: 650,
      color: tokens.foreground,
      tracking: 5,
    });
  } else {
    textBlock(ctx, copy.title, MARGIN, 122, {
      size: 34,
      weight: 650,
      color: tokens.foreground,
      maxWidth: CONTENT,
      maxLines: 2,
    });
    drawSignalBand(ctx, frame);
  }

  text(ctx, copy.hint, MARGIN, 262, {
    size: 11,
    weight: 500,
    color: tokens.muted,
    maxWidth: CONTENT,
  });
}

// Hai chấm lệch chéo, đúng nhịp của hai chấm sáng trong dấu nhận diện. Chấm sau
// thở nhẹ khi máy đang thực sự làm việc.
function drawSignatureDots(ctx, { tokens, state, time }) {
  const busy = state === "listening" || state === "speaking" || state === "thinking" || state === "evaluating";
  const pulse = busy ? 0.55 + 0.45 * Math.abs(Math.sin(time * 3.2)) : 0.35;
  fillCircle(ctx, MARGIN + 3, 73, 2.6, tokens.accent);
  fillCircle(ctx, MARGIN + 10, 76, 2.6, withAlpha(tokens.accent, pulse));
}

function drawSignalBand(ctx, frame) {
  if (METER_STATES.has(frame.state)) return drawMeter(ctx, frame);
  return drawTrack(ctx, frame);
}

// Dãy hình vuông bo góc theo tỉ lệ của dấu Veetee, cao dần theo biên độ thật.
function drawMeter(ctx, { tokens, state, time, level }) {
  const count = 9;
  const size = 14;
  const gap = (CONTENT - count * size) / (count - 1);
  const baseline = BAND.y + BAND.height;
  const intensity =
    state === "listening" ? 0.35 + 0.65 * level : state === "speaking" ? 0.5 + 0.5 * level : state === "idle" ? 0.16 : 0.08;
  const rate = state === "speaking" ? 7.2 : state === "listening" ? 5.4 : 1.3;
  const quiet = state === "idle" || state === "closing";

  for (let index = 0; index < count; index += 1) {
    const window = 0.55 + 0.45 * Math.sin((Math.PI * (index + 0.5)) / count);
    const wave = 0.4 + 0.6 * Math.abs(Math.sin(time * rate + index * 0.62));
    const height = clamp(size * 0.42 + BAND.height * intensity * window * wave, size * 0.42, BAND.height);
    const x = MARGIN + index * (size + gap);
    fillRound(ctx, x, baseline - height, size, height, size * MARK_RADIUS_RATIO, quiet ? tokens.accentDim : tokens.accent);
  }
}

// Mọi trạng thái không có âm thanh dùng chung một rãnh duy nhất: đang chạy thì
// có vệt sáng chạy, đang cảnh báo thì rãnh đứng yên và đổi tông.
function drawTrack(ctx, { tokens, state, time }) {
  const y = BAND.y + BAND.height - 8;
  const height = 6;
  fillRound(ctx, MARGIN, y, CONTENT, height, height / 2, tokens.panelRaised);

  if (state === "pairing_recovery") {
    // Rãnh đứt đoạn: hình dạng mới là tín hiệu, không phải riêng màu.
    for (let index = 0; index < 5; index += 1) {
      fillRound(ctx, MARGIN + index * 42, y, 28, height, height / 2, tokens.accent);
    }
    return;
  }
  if (state === "aborting") {
    fillRound(ctx, MARGIN, y, CONTENT * (1 - ((time * 0.8) % 1)), height, height / 2, tokens.accent);
    return;
  }

  const width = state === "evaluating" || state === "thinking" ? 56 : 72;
  const travel = CONTENT - width;
  const phase = (time * 0.55) % 2;
  const position = phase < 1 ? phase : 2 - phase;
  fillRound(ctx, MARGIN + travel * position, y, width, height, height / 2, tokens.accent);
}
