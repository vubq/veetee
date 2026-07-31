// Composition `quiet` — product name Robot Face, demo name "Đôi mắt".
//
// Hướng: tối giản, không khung. Khuôn mặt nổi thẳng trên nền, tách khối bằng
// khoảng trắng. Bỏ khung cũng bỏ luôn hai vòng quầng sáng — thứ ngốn SPI nhiều
// nhất mà không mang thông tin nào chỗ khác chưa nói.
//
// Dấu riêng của Veetee: hai chấm sáng trong dấu nhận diện nằm lệch chéo nhau,
// nên hai con mắt cũng đặt đúng độ lệch đó — mắt phải thấp hơn mắt trái 2px.
// Khuôn mặt này *chính là* dấu nhận diện phóng to, không phải một con robot
// chung chung có gắn thêm logo ở góc.
//
// Chớp mắt vẽ bằng mí trượt chứ không co chiều cao con mắt, để bờ mi giữ nguyên
// độ nét thay vì bị bóp méo.

import { capsule, clamp, easeInOut, fillCircle, fillRound, text, veeMark, withAlpha } from "../draw.js";

const EYE = { leftX: 82, rightX: 158, centerY: 128, width: 56, height: 76 };
// Độ lệch chéo giữa hai chấm sáng của dấu nhận diện.
const MARK_DOT_SKEW = 2;
const MOUTH_Y = 196;

const SCANNING = new Set(["wifi_configuring", "network_connecting", "connecting", "starting"]);
const SEARCHING = new Set(["evaluating", "thinking"]);

export function renderEyes(ctx, frame) {
  const { tokens, copy, state, time, level, activationCode } = frame;

  veeMark(ctx, 20, 13, 15, tokens.accent, tokens.background);
  text(ctx, copy.number, 220, 26, {
    size: 11,
    weight: 600,
    color: tokens.muted,
    tracking: 1.2,
    align: "right",
  });

  // Cả khuôn mặt trôi hơn 1px theo nhịp thở chậm. Rất nhỏ, nhưng đây là thứ
  // tách "đang bật" khỏi "đang sống".
  const breath = Math.sin(time * 0.9) * 1.2;
  const blink = blinkAmount(time, state);
  const look = lookOffset(time, state);
  const geometry = eyeGeometry(state, level);
  const centerY = EYE.centerY + breath + look.y;

  drawEye(ctx, EYE.leftX + look.x, centerY, geometry, blink, tokens, state);
  drawEye(ctx, EYE.rightX + look.x, centerY + MARK_DOT_SKEW, geometry, blink, tokens, state);
  drawAlertBrows(ctx, tokens, state, geometry, centerY, blink);

  if (state === "activating") {
    text(ctx, String(activationCode ?? "284716"), 120, MOUTH_Y + 6 + breath, {
      size: 30,
      weight: 650,
      color: tokens.foreground,
      tracking: 5,
      align: "center",
    });
  } else if (state === "speaking" || state === "idle") {
    drawMouth(ctx, tokens, state, level, time, breath);
  }

  text(ctx, copy.title, 120, 240, { size: 15, weight: 600, color: tokens.foreground, align: "center", maxWidth: 200 });
  if (copy.hint) {
    text(ctx, copy.hint, 120, 262, { size: 11, weight: 500, color: tokens.muted, align: "center", maxWidth: 200 });
  }
}

function eyeGeometry(state, level) {
  let width = EYE.width;
  let height = EYE.height;
  if (state === "listening") {
    width = 60;
    height = 84;
  }
  if (state === "speaking") height = 76 - 16 * level;
  if (state === "starting") height = 48;
  if (state === "aborting") height = 40;
  return { width, height, radius: width / 2 };
}

function drawEye(ctx, centerX, centerY, geometry, blink, tokens, state) {
  const { width, height, radius } = geometry;
  const left = centerX - width / 2;
  const top = centerY - height / 2;

  // Lớp 1: hình dạng con mắt.
  fillRound(ctx, left, top, width, height, radius, tokens.accent);

  // Lớp 2: mống mắt tối hơn, cho chiều sâu thay vì một mảng màu đặc.
  const irisWidth = width * 0.54;
  const irisHeight = height * 0.44;
  fillRound(
    ctx,
    centerX - irisWidth / 2,
    centerY - irisHeight / 2 + height * 0.07,
    irisWidth,
    irisHeight,
    irisWidth / 2,
    withAlpha(tokens.background, 0.5),
  );

  // Lớp 3: đốm sáng chuyên biệt.
  fillCircle(ctx, left + width * 0.31, top + height * 0.25, width * 0.1, tokens.accentBright);

  // Lớp 4: mí trên trượt xuống — đây là cái chớp mắt.
  if (blink > 0.01) {
    const lid = height * blink;
    fillRound(ctx, left - 1, top - 1, width + 2, lid + 1, radius, tokens.background);
    if (blink > 0.78) capsule(ctx, left + 4, top + lid, left + width - 4, top + lid, 2.6, tokens.accentDim);
  }

  // Mí dưới nhíu lại khi đang tập trung nghe.
  if (state === "listening") {
    const squint = height * 0.15;
    fillRound(ctx, left - 1, top + height - squint, width + 2, squint + 1, radius * 0.8, tokens.background);
  }
}

// Chỉ vẽ chân mày cho hai trạng thái cảnh báo. Hình dạng mới là tín hiệu chính,
// vì màu nhấn giữ nguyên một tông ở mọi trạng thái bình thường.
function drawAlertBrows(ctx, tokens, state, geometry, centerY, blink) {
  if (blink > 0.6) return;
  if (state !== "pairing_recovery" && state !== "aborting") return;
  const browY = centerY - geometry.height / 2 - 13;
  capsule(ctx, EYE.leftX - 19, browY - 4, EYE.leftX + 19, browY + 5, 4, tokens.accent);
  capsule(ctx, EYE.rightX - 19, browY + 5 + MARK_DOT_SKEW, EYE.rightX + 19, browY - 4 + MARK_DOT_SKEW, 4, tokens.accent);
}

function drawMouth(ctx, tokens, state, level, time, breath) {
  let width = 30;
  let height = 6;
  if (state === "speaking") {
    width = 28 + 20 * level;
    height = 6 + 16 * level;
  } else {
    width = 30 + 3 * Math.sin(time * 1.4);
  }
  const y = MOUTH_Y + breath;
  fillRound(ctx, 120 - width / 2, y - height / 2, width, height, Math.min(width, height) / 2, tokens.accent);
  if (state === "speaking" && level > 0.5) {
    fillRound(
      ctx,
      120 - width / 2 + 4,
      y - height / 2 + 3,
      width - 8,
      height - 6,
      (height - 6) / 2,
      withAlpha(tokens.background, 0.55),
    );
  }
}

// 0 = mở hẳn, 1 = nhắm hẳn. Chớp đôi thỉnh thoảng, vì mắt thật không chớp đều.
function blinkAmount(time, state) {
  if (state === "closing") return 1;
  if (state === "starting") return 0.35;
  const period = 3.4;
  const cycle = Math.floor(time / period);
  const phase = (time % period) / period;
  const openAt = 0.93;
  const double = cycle % 3 === 2;
  const hit = (start, width) => {
    if (phase < start || phase > start + width) return 0;
    return Math.sin(((phase - start) / width) * Math.PI);
  };
  return clamp(Math.max(hit(openAt, 0.055), double ? hit(openAt - 0.09, 0.045) : 0), 0, 1);
}

function lookOffset(time, state) {
  // Vi chuyển động: mắt thật không bao giờ đứng tuyệt đối yên.
  const micro = Math.sin(time * 7.3) * 0.4 + Math.sin(time * 3.1) * 0.3;
  if (SEARCHING.has(state)) {
    const cycle = time / 0.7;
    const step = Math.floor(cycle);
    const progress = easeInOut(clamp((cycle - step) * 3, 0, 1));
    const from = step % 2 === 0 ? -10 : 10;
    return { x: from - 2 * from * progress + micro, y: -1 };
  }
  if (SCANNING.has(state)) return { x: Math.sin(time * 1.1) * 11 + micro, y: 0 };
  if (state === "activating") return { x: micro, y: 4 };
  if (state === "listening") return { x: micro, y: -2 };
  return { x: micro, y: 0 };
}
