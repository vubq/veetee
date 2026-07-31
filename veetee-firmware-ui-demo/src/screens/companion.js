// Composition `monolith` — product name Companion, demo name "Hiyori Momose".
//
// Live2D never runs on the ESP32-S3. The character arrives as VTCLIP1 frame
// sequences produced on a PC, and the device does two things per frame:
//   1. blit the decoded RGB565 clip frame into the framebuffer;
//   2. draw the HUD (kicker, title, hint, pairing code) on top with the same
//      vector + .vfont primitives as the other two compositions.
//
// Keeping text out of the baked frames is what makes localization, pairing
// codes and error copy possible without re-exporting the character.

import { clamp, fillCircle, fillRound, scrim, strokeArc, strokeRound, text, veeMark, withAlpha } from "../draw.js";
import { clipFrame, clipForState } from "../clip.js";
import { PANEL } from "../contract.js";

const STAGE = { x: 0, y: 0, width: PANEL.width, height: 212 };

export function renderCompanion(ctx, frame) {
  const { clipSet } = frame;
  if (clipSet) {
    drawClip(ctx, frame, clipSet);
  } else {
    drawEmptySlot(ctx, frame);
  }
  drawHud(ctx, frame);
}

function drawClip(ctx, frame, clipSet) {
  const { state, time, level } = frame;
  const entry = clipForState(clipSet, state);
  if (!entry) return;
  const { clip } = entry;
  const index = Math.floor(time * clip.fps);
  ctx.putImageData(clipFrame(clip, index), entry.x ?? 0, entry.y ?? 0);

  const mouth = clipSet.overlays?.mouth;
  if (!mouth || state !== "speaking") return;
  const levels = mouth.levels ?? mouth.clip.frameCount;
  const step = clamp(Math.round(level * (levels - 1)), 0, levels - 1);
  // Đầu nhân vật cử động suốt clip, nên khung miệng được chụp riêng cho từng
  // frame nền. Tra sai chỉ số ở đây là dán miệng của pose khác lên mặt.
  const mouthIndex = mouth.per_frame ? baseFrameIndex(clip, index) * levels + step : step;
  ctx.putImageData(clipFrame(mouth.clip, mouthIndex), mouth.x ?? 0, mouth.y ?? 0);
}

function baseFrameIndex(clip, index) {
  return ((index % clip.frameCount) + clip.frameCount) % clip.frameCount;
}

function drawEmptySlot(ctx, { tokens, time }) {
  const bob = Math.sin(time * 1.15) * 2.4;

  ctx.save();
  ctx.setLineDash([5, 5]);
  strokeRound(ctx, 16, 38, 208, 166, 26, tokens.hairline, 1);
  ctx.restore();

  // Deliberately abstract: the demo never draws a stand-in for the licensed
  // character. This only proves the player path and the frame cadence.
  fillCircle(ctx, 120, 104 + bob, 27, tokens.panelRaised);
  strokeArc(ctx, 120, 190 + bob, 52, Math.PI, Math.PI * 2, tokens.panelRaised, 22, "butt");
  fillCircle(ctx, 111, 100 + bob, 3.2, tokens.accentDim);
  fillCircle(ctx, 129, 100 + bob, 3.2, tokens.accentDim);

  text(ctx, "CHƯA NẠP CLIP", 120, 166, {
    size: 11,
    weight: 700,
    color: tokens.accent,
    tracking: 1.4,
    align: "center",
  });
  text(ctx, "assets/hiyori/*.vclip", 120, 183, {
    size: 9.5,
    weight: 500,
    color: tokens.muted,
    align: "center",
  });
  text(ctx, ".cmo3 · Cubism · PNG seq · 240×280 · VTCLIP1", 120, 199, {
    size: 8.5,
    weight: 500,
    color: tokens.muted,
    align: "center",
    maxWidth: 200,
  });
}

function drawHud(ctx, frame) {
  const { tokens, copy, state, activationCode, level, time } = frame;

  scrim(ctx, 0, 0, PANEL.width, 40, tokens.background, 0.92, 0);
  veeMark(ctx, 14, 13, 14, tokens.accent, tokens.background);
  text(ctx, copy.kicker, 34, 24, {
    size: 9,
    weight: 600,
    color: tokens.accent,
    tracking: 1.2,
    uppercase: true,
    maxWidth: 150,
  });
  text(ctx, copy.number, 226, 24, { size: 10, weight: 700, color: tokens.foreground, tracking: 1.2, align: "right" });

  scrim(ctx, 0, STAGE.height - 30, PANEL.width, 32, tokens.background, 0, 0.94);
  ctx.fillStyle = tokens.background;
  ctx.fillRect(0, STAGE.height + 2, PANEL.width, PANEL.height - STAGE.height - 2);

  if (state === "activating") {
    fillRound(ctx, 44, 218, 152, 32, 13, tokens.accentSoft);
    strokeRound(ctx, 44, 218, 152, 32, 13, tokens.accent, 1);
    text(ctx, String(activationCode ?? "284716"), 120, 241, {
      size: 20,
      weight: 600,
      color: tokens.foreground,
      tracking: 4,
      align: "center",
    });
  } else {
    text(ctx, copy.title, 120, 236, {
      size: 17,
      weight: 600,
      color: tokens.foreground,
      align: "center",
      maxWidth: 216,
    });
    if (state === "speaking") drawMouthLevel(ctx, tokens, level, time);
  }

  if (copy.hint) {
    text(ctx, copy.hint, 120, 266, {
      size: 10,
      weight: 500,
      color: tokens.muted,
      align: "center",
      maxWidth: 216,
    });
  }
}

// Four discrete mouth levels: the same index the firmware uses to pick the
// mouth overlay frame from the TTS amplitude envelope.
function drawMouthLevel(ctx, tokens, level, time) {
  const active = clamp(Math.round(level * 3), 0, 3);
  for (let index = 0; index < 4; index += 1) {
    const x = 102 + index * 12;
    const on = index <= active;
    fillRound(ctx, x, 246, 8, 4, 2, on ? tokens.accent : withAlpha(tokens.accent, 0.22));
  }
  void time;
}
