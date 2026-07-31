#!/usr/bin/env node
// Self-check cho demo: node tools/check.mjs
//
// Không cần cài gì. Kiểm 6 nhóm:
//   1. contract mirror đủ 13 state x 3 giao diện
//   2. VTCLIP1 encode/decode roundtrip pixel-exact
//   3. container hỏng phải bị từ chối
//   4. clip set: state_map, overlay, giới hạn panel và ngân sách slot
//   5. ba màn hình chạy hết mọi state mà không tràn khỏi 240x280
//   6. PNG sequence -> .vclip qua tools/pack-clip.mjs
//   7. id trong HTML khớp querySelector trong JS

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { deflateSync } from "node:zlib";

const ROOT = new URL("..", import.meta.url);
const resolve = (path) => new URL(path, ROOT);

// The reader builds ImageData; supply the minimum surface Node needs.
globalThis.ImageData = class {
  constructor(width, height) {
    this.width = width;
    this.height = height;
    this.data = new Uint8ClampedArray(width * height * 4);
  }
};

const contract = await import(resolve("src/contract.js"));
const codec = await import(resolve("src/clip-codec.js"));
const reader = await import(resolve("src/clip.js"));
const budget = await import(resolve("src/clip-budget.js"));
const { renderOs } = await import(resolve("src/screens/os.js"));
const { renderEyes } = await import(resolve("src/screens/eyes.js"));
const { renderCompanion } = await import(resolve("src/screens/companion.js"));

const RENDERERS = { signal: renderOs, monolith: renderCompanion, quiet: renderEyes };
const { width: WIDTH, height: HEIGHT } = contract.PANEL;
const results = [];

function check(name, run) {
  try {
    const detail = run();
    results.push({ name, detail: detail ?? "ok" });
  } catch (error) {
    results.push({ name, error });
  }
}

async function checkAsync(name, run) {
  try {
    const detail = await run();
    results.push({ name, detail: detail ?? "ok" });
  } catch (error) {
    results.push({ name, error });
  }
}

// ------------------------------------------------------------ 1. contract

// Mọi token màn hình dùng tới. Thiếu một cái là canvas nhận `undefined` và im
// lặng bỏ qua nét vẽ, nên phải chặn ở đây chứ không để phát hiện bằng mắt.
const REQUIRED_TOKENS = [
  "background",
  "foreground",
  "accent",
  "panel",
  "panelRaised",
  "inset",
  "hairline",
  "secondary",
  "muted",
  "accentSoft",
  "accentDim",
  "accentBright",
];

check("contract mirror", () => {
  assert.equal(contract.STATE_IDS.length, 13);
  assert.equal(contract.COMPOSITIONS.length, 3);
  assert.deepEqual(
    contract.PALETTE_SOURCES.map((source) => source.id),
    ["web", "pack"],
  );
  for (const state of contract.STATE_IDS) {
    assert.ok(contract.SCREEN_COPY[state], `thiếu kScreenCopy cho ${state}`);
    for (const composition of contract.COMPOSITIONS) {
      assert.ok(composition.palette[state], `${composition.id} thiếu palette ${state}`);
      assert.ok(composition.localized[state], `${composition.id} thiếu vi-VN ${state}`);
      const ascii = contract.copyFor(composition, state, "ascii");
      const vietnamese = contract.copyFor(composition, state, "vi-VN");
      assert.equal(ascii.number, vietnamese.number);
      assert.ok(vietnamese.title.length > 0);
      for (const source of ["web", "pack"]) {
        const tokens = contract.tokensFor(composition, state, source);
        for (const key of REQUIRED_TOKENS) {
          assert.match(tokens[key] ?? "", /^rgb\(/, `${source}/${composition.id}/${state}: token "${key}" hỏng`);
        }
      }
    }
  }
  // Bảng màu web phải là giá trị nguyên văn của docs/22, không phải phối lại.
  const web = contract.tokensFor(contract.COMPOSITIONS[0], "idle", "web");
  assert.equal(web.background, "rgb(13, 23, 25)", "canvas phải đúng #0d1719");
  assert.equal(web.accent, "rgb(255, 118, 81)", "mọi state bình thường dùng một accent cam #ff7651");
  assert.equal(contract.tokensFor(contract.COMPOSITIONS[0], "thinking", "web").accent, web.accent, "accent không được đổi theo state bình thường");
  assert.equal(contract.tokensFor(contract.COMPOSITIONS[0], "aborting", "web").accent, "rgb(255, 128, 111)", "chỉ cảnh báo mới đổi tông");
  
  return `13 state x 3 giao diện x 2 bảng màu, ${REQUIRED_TOKENS.length} token mỗi bộ`;
});

// --------------------------------------------------------------- 2/3. codec

function syntheticFrame(seed) {
  const pixels = new Uint16Array(WIDTH * HEIGHT);
  for (let y = 0; y < HEIGHT; y += 1) {
    for (let x = 0; x < WIDTH; x += 1) {
      let value = 0;
      if (y < 40) value = 0x1965;
      else if (y < 200 && x > 60 && x < 180) value = ((x & 0x1f) << 11) | ((y & 0x3f) << 5) | ((x ^ y) & 0x1f);
      else if (y > 260) value = (x * 7 + y * 13) & 0xffff;
      // Vùng chuyển động nhỏ giữa các frame, giống nhân vật nhúc nhích: đây là
      // thứ quyết định delta có ăn hay không.
      if (x >= 20 + seed * 8 && x < 60 + seed * 8 && y >= 210 && y < 250) value = 0xc9ad;
      pixels[y * WIDTH + x] = value;
    }
  }
  return pixels;
}

const sources = [0, 1, 2].map(syntheticFrame);
const clipBytes = codec.writeClip(sources.map(codec.encodeFrame), { width: WIDTH, height: HEIGHT, fps: 12 });
const asBuffer = (bytes) => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);

// Cùng nội dung, mã hoá delta. Frame 0 là keyframe, phần còn lại vá lên frame trước.
const deltaBytes = codec.writeClip(codec.encodeFrameSequence(sources), {
  width: WIDTH,
  height: HEIGHT,
  fps: 12,
  flags: codec.CLIP_FLAG_DELTA,
});

check("VTCLIP1 roundtrip", () => {
  const clip = reader.decodeClip(asBuffer(clipBytes), "check.vclip");
  assert.equal(clip.width, WIDTH);
  assert.equal(clip.height, HEIGHT);
  assert.equal(clip.frameCount, 3);
  for (let index = 0; index < sources.length; index += 1) {
    const image = reader.clipFrame(clip, index);
    for (let pixel = 0; pixel < WIDTH * HEIGHT; pixel += 1) {
      const base = pixel * 4;
      const repacked = codec.pack565(image.data[base], image.data[base + 1], image.data[base + 2]);
      assert.equal(repacked, sources[index][pixel], `frame ${index} pixel ${pixel}`);
      assert.equal(image.data[base + 3], 255);
    }
  }
  assert.ok(reader.clipFrame(clip, 3) === reader.clipFrame(clip, 0), "index phải lặp vòng");
  assert.ok(reader.clipFrame(clip, -1) === reader.clipFrame(clip, 2));
  return `3 frame pixel-exact, ${(clipBytes.length / 3 / 1024).toFixed(1)} KiB/frame`;
});

check("delta frame roundtrip", () => {
  const clip = reader.decodeClip(asBuffer(deltaBytes), "delta.vclip");
  assert.equal(clip.delta, true, "cờ delta phải được đọc ra");

  const verify = (label) => {
    for (let index = 0; index < sources.length; index += 1) {
      const image = reader.clipFrame(clip, index);
      for (let pixel = 0; pixel < WIDTH * HEIGHT; pixel += 1) {
        const base = pixel * 4;
        const repacked = codec.pack565(image.data[base], image.data[base + 1], image.data[base + 2]);
        assert.equal(repacked, sources[index][pixel], `${label}: frame ${index} pixel ${pixel}`);
      }
    }
  };
  verify("tuần tự");

  // Truy cập lùi và nhảy cóc phải dựng lại từ keyframe chứ không trả frame sai.
  const fresh = reader.decodeClip(asBuffer(deltaBytes), "delta.vclip");
  const last = reader.clipFrame(fresh, 2);
  const first = reader.clipFrame(fresh, 0);
  assert.notEqual(last, first);
  for (let pixel = 0; pixel < WIDTH * HEIGHT; pixel += 1) {
    const base = pixel * 4;
    assert.equal(codec.pack565(last.data[base], last.data[base + 1], last.data[base + 2]), sources[2][pixel]);
  }

  const ratio = clipBytes.length / deltaBytes.length;
  return `khớp pixel cả tuần tự lẫn nhảy cóc · nhỏ hơn bản keyframe ${ratio.toFixed(1)}×`;
});

check("từ chối container hỏng", () => {
  const mutate = (index, value) => {
    const copy = Uint8Array.from(clipBytes);
    copy[index] = value;
    return asBuffer(copy);
  };
  assert.throws(() => reader.decodeClip(mutate(1, 0x41), "magic"), /magic/);
  // bit 0 giờ là cờ delta hợp lệ; bit chưa định nghĩa vẫn phải bị từ chối.
  assert.throws(() => reader.decodeClip(mutate(16, 0x02), "flags"), /flags/);
  assert.throws(() => reader.decodeClip(mutate(28, 0x01), "reserved"), /flags\/reserved/);
  assert.throws(() => reader.decodeClip(mutate(clipBytes.length - 1, clipBytes.at(-1) ^ 0xff), "crc"), /CRC32/);
  const truncated = clipBytes.slice(0, clipBytes.length - 4);
  assert.throws(() => reader.decodeClip(asBuffer(truncated), "short"), /payload_len/);
  return "magic, flags, CRC32, truncation";
});

// ------------------------------------------------------- 3b. ngân sách clip

check("ngân sách quyết định fps", () => {
  // Chạy thật các hàm này chứ không chỉ kiểm cú pháp: lỗi biến chưa import chỉ
  // nổ lúc gọi, mà đó chính là loại lỗi đã lọt ra tới trình duyệt một lần.
  const payloadLength = clipBytes.length - codec.CLIP_HEADER_BYTES - 3 * 4;
  const summed = [0, 1, 2].reduce((total, index) => total + budget.frameByteLength(clipBytes, index), 0);
  assert.equal(summed, payloadLength, "tổng độ dài frame phải bằng payload");
  assert.throws(() => budget.frameByteLength(clipBytes, 99), RangeError);

  const clips = [
    { id: "idle", seconds: 2.0 },
    { id: "speaking", seconds: 1.35 },
  ];
  // Số frame chẵn, không bao giờ dưới ngưỡng tối thiểu.
  assert.equal(budget.frameCountFor(clips[0], 12, 1), 24);
  assert.equal(budget.frameCountFor(clips[0], 12, 0.5), 12);
  assert.equal(budget.frameCountFor(clips[1], 4, 0.25), budget.MIN_FRAMES);
  for (const fps of budget.FPS_LADDER) {
    assert.equal(budget.frameCountFor(clips[0], fps, 1) % 2, 0);
  }

  const model = budget.costModel([
    { id: "idle", frames: 24, base: fakeClip(40_000, 23 * 9_000), mouth: fakeClip(24 * 4 * 800, 0) },
    { id: "speaking", frames: 16, base: fakeClip(40_000, 15 * 12_000), mouth: null },
  ]);
  assert.equal(model.get("idle").keyBytes, 40_000);
  assert.equal(model.get("idle").deltaBytes, 9_000);
  assert.equal(model.get("speaking").mouthPerFrame, 0);

  // Dung lượng ước tính phải tăng theo fps, nếu không thì cả bài toán vô nghĩa.
  let previous = 0;
  for (const fps of [...budget.FPS_LADDER].reverse()) {
    const bytes = budget.estimateBytes(clips, model, fps, 1);
    assert.ok(bytes > previous, `estimateBytes phải tăng theo fps (${fps})`);
    previous = bytes;
  }

  // Ngân sách rộng: giữ nguyên yêu cầu. Ngân sách hẹp: hạ fps, không vượt yêu cầu.
  const roomy = budget.planWithinBudget(clips, model, 12, 1, 8 * 1024 * 1024);
  assert.deepEqual(roomy, { fps: 12, scale: 1 });
  const tight = budget.planWithinBudget(clips, model, 12, 1, 2 * 1024 * 1024 * budget.BUDGET_HEADROOM);
  assert.ok(tight.fps <= 12, "không bao giờ vượt fps người dùng yêu cầu");
  assert.ok(budget.estimateBytes(clips, model, tight.fps, tight.scale) <= 2 * 1024 * 1024 * budget.BUDGET_HEADROOM);
  const impossible = budget.planWithinBudget(clips, model, 12, 1, 1024);
  assert.equal(impossible.fps, budget.FPS_LADDER[budget.FPS_LADDER.length - 1]);

  assert.equal(budget.UI_SLOT_PRESETS[0].bytes, 2 * 1024 * 1024);
  return `fps chọn được ở ngân sách 2 MiB: ${tight.fps} fps, vòng lặp ×${tight.scale}`;
});

function fakeClip(keyBytes, deltaBytesTotal) {
  // Chỉ cần header + bảng offset đúng; nội dung payload không quan trọng.
  const frames = deltaBytesTotal === 0 ? [keyBytes] : [keyBytes, deltaBytesTotal];
  return codec.writeClip(
    frames.map((size) => new Uint8Array(size)),
    { width: 8, height: 8, fps: 12 },
  );
}

// ----------------------------------------------------------- 4. clip set

function recordingContext() {
  const blits = [];
  const images = [];
  const bounds = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  let fontSize = 12;
  let matrix = [1, 0, 0, 1, 0, 0];
  const stack = [];
  const mark = (x, y) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error(`toạ độ không hữu hạn ${x},${y}`);
    const tx = matrix[0] * x + matrix[2] * y + matrix[4];
    const ty = matrix[1] * x + matrix[3] * y + matrix[5];
    bounds.minX = Math.min(bounds.minX, tx);
    bounds.minY = Math.min(bounds.minY, ty);
    bounds.maxX = Math.max(bounds.maxX, tx);
    bounds.maxY = Math.max(bounds.maxY, ty);
  };
  const multiply = (m) => {
    const [a, b, c, d, e, f] = matrix;
    matrix = [
      a * m[0] + c * m[1], b * m[0] + d * m[1],
      a * m[2] + c * m[3], b * m[2] + d * m[3],
      a * m[4] + c * m[5] + e, b * m[4] + d * m[5] + f,
    ];
  };
  return {
    blits,
    images,
    bounds,
    globalAlpha: 1,
    set font(value) {
      fontSize = Number.parseFloat(value.match(/([\d.]+)px/)?.[1] ?? 12);
    },
    get font() {
      return `${fontSize}px`;
    },
    fillRect: (x, y, w, h) => { mark(x, y); mark(x + w, y + h); },
    clearRect: () => {},
    beginPath: () => {},
    closePath: () => {},
    moveTo: mark,
    lineTo: mark,
    arcTo: (x1, y1, x2, y2) => { mark(x1, y1); mark(x2, y2); },
    arc: (x, y, r) => { mark(x - r, y - r); mark(x + r, y + r); },
    fill: () => {},
    stroke: () => {},
    save: () => stack.push([...matrix]),
    restore: () => { matrix = stack.pop() ?? [1, 0, 0, 1, 0, 0]; },
    translate: (x, y) => multiply([1, 0, 0, 1, x, y]),
    rotate: (angle) => multiply([Math.cos(angle), Math.sin(angle), -Math.sin(angle), Math.cos(angle), 0, 0]),
    setLineDash: () => {},
    setTransform: () => { matrix = [1, 0, 0, 1, 0, 0]; },
    createLinearGradient: () => ({ addColorStop: () => {} }),
    measureText: (value) => ({ width: [...String(value)].length * fontSize * 0.56 }),
    fillText: (value, x, y) => {
      const width = [...String(value)].length * fontSize * 0.56;
      mark(x, y - fontSize * 0.8);
      mark(x + width, y + fontSize * 0.25);
    },
    putImageData: (image, x, y) => {
      blits.push([image.width, image.height, x, y]);
      images.push(image);
      mark(x, y);
      mark(x + image.width, y + image.height);
    },
  };
}

await checkAsync("clip set: state_map, overlay, giới hạn", async () => {
  const template = JSON.parse(readFileSync(new URL("assets/hiyori/manifest.example.json", ROOT), "utf8"));

  // Tool capture cắt clip theo bóng nhân vật, nên clip nhỏ hơn panel và có toạ
  // độ đặt là trường hợp thường, không phải ngoại lệ. Kiểm đúng trường hợp đó.
  const CROP = { x: 30, y: 40, width: 180, height: 220 };
  const LEVELS = 4;
  const BASE_FRAMES = 2;
  const cropped = codec.writeClip(
    [0, 1].map((seed) => {
      const pixels = new Uint16Array(CROP.width * CROP.height);
      for (let index = 0; index < pixels.length; index += 1) pixels[index] = (index + seed) % 2 ? 0xc9ad : 0x1166;
      return codec.encodeFrame(pixels);
    }),
    { width: CROP.width, height: CROP.height, fps: 12 },
  );
  const manifest = {
    ...template,
    clips: Object.fromEntries(
      Object.entries(template.clips).map(([id, entry]) => [id, { ...entry, x: CROP.x, y: CROP.y }]),
    ),
    overlays: {
      mouth: { file: "mouth.vclip", x: 88, y: 150, levels: LEVELS, per_frame: true, base: "speaking" },
    },
  };
  // Overlay per-frame: 2 frame nền × 4 mức miệng, phẳng hoá theo
  // baseIndex * levels + level như tool capture sinh ra.
  const mouth = codec.writeClip(
    Array.from({ length: BASE_FRAMES * LEVELS }, (_, index) => {
      const level = index % LEVELS;
      const pixels = new Uint16Array(64 * 44).fill(0x1166);
      for (let y = 22 - level * 4; y < 22 + level * 4; y += 1) {
        for (let x = 20; x < 44; x += 1) pixels[y * 64 + x] = 0xc9ad;
      }
      return codec.encodeFrame(pixels);
    }),
    { width: 64, height: 44, fps: 12 },
  );
  const read = async (file) => (file === "mouth.vclip" ? asBuffer(mouth) : asBuffer(cropped));
  const set = await reader.hydrateClipSet(manifest, read);
  assert.equal(Object.keys(set.clips).length, 6);
  assert.equal(Object.keys(set.overlays).length, 1);

  const composition = contract.compositionById("monolith");
  const ctx = recordingContext();
  for (const state of contract.STATE_IDS) {
    assert.ok(reader.clipForState(set, state), `state_map không giải được ${state}`);
    renderCompanion(ctx, {
      composition,
      tokens: contract.tokensFor(composition, state),
      state,
      copy: contract.copyFor(composition, state, "vi-VN"),
      locale: "vi-VN",
      activationCode: "284716",
      time: 1.25,
      frameIndex: 15,
      level: 0.8,
      clipSet: set,
    });
  }
  assert.equal(ctx.blits.length, 14, "13 frame nền + 1 overlay miệng");
  assert.deepEqual(
    ctx.blits.filter(([w, h]) => w === 64 && h === 44),
    [[64, 44, 88, 150]],
    "overlay miệng chỉ blit khi speaking, đúng vị trí manifest",
  );
  assert.deepEqual(
    [...new Set(ctx.blits.filter(([w]) => w === CROP.width).map((blit) => blit.join(",")))],
    [[CROP.width, CROP.height, CROP.x, CROP.y].join(",")],
    "clip đã crop phải blit đúng toạ độ trong manifest",
  );

  // Chỉ số overlay phải là baseIndex * levels + level. time 1.25 @ 12 fps ->
  // frame nền 15 % 2 = 1; level 0.8 -> mức 2; nên phải là frame 1*4+2 = 6.
  const mouthImage = ctx.images[ctx.blits.findIndex(([w, h]) => w === 64 && h === 44)];
  assert.ok(
    mouthImage === reader.clipFrame(set.overlays.mouth.clip, 6),
    "overlay per-frame phải tra bằng baseIndex * levels + level",
  );

  const outside = { ...manifest, overlays: { mouth: { file: "mouth.vclip", x: 200, y: 150, levels: 4 } } };
  await assert.rejects(() => reader.hydrateClipSet(outside, read), /ngo/);

  const mismatched = {
    ...manifest,
    overlays: { mouth: { ...manifest.overlays.mouth, levels: 3 } },
  };
  await assert.rejects(() => reader.hydrateClipSet(mismatched, read), /clip nền/);

  const oversize = {
    ...manifest,
    overlays: {},
    clips: Object.fromEntries(
      Array.from({ length: 900 }, (_, i) => [`c${i}`, { file: "idle.vclip", fps: 12, x: CROP.x, y: CROP.y }]),
    ),
  };
  await assert.rejects(() => reader.hydrateClipSet(oversize, read), /4 MiB/);

  assert.equal(reader.slotSizeFor(1.5 * 1024 * 1024), 2);
  assert.equal(reader.slotSizeFor(2.5 * 1024 * 1024), 3);
  assert.equal(reader.slotSizeFor(5 * 1024 * 1024), null);
  return "13 state, overlay đúng vị trí, chặn tràn panel và chặn quá mốc 4 MiB";
});

// -------------------------------------------------------- 5. render smoke

check("ba màn hình vẽ trong 240x280", () => {
  let frames = 0;
  const overflows = [];
  for (const composition of contract.COMPOSITIONS) {
    for (const state of contract.STATE_IDS) {
      for (const locale of ["ascii", "vi-VN"]) {
        for (const frameIndex of [0, 1, 3, 7, 11, 19, 47]) {
          const source = frameIndex % 2 === 0 ? "web" : "pack";
          const ctx = recordingContext();
          RENDERERS[composition.id](ctx, {
            composition,
            tokens: contract.tokensFor(composition, state, source),
            state,
            copy: contract.copyFor(composition, state, locale),
            locale,
            activationCode: "284716",
            time: frameIndex / 30,
            frameIndex,
            level: (frameIndex % 5) / 4,
            clipSet: null,
          });
          frames += 1;
          // Nét bo tròn có thể lệch dưới một pixel; quá vài pixel là tràn thật.
          const slack = 3;
          const { minX, minY, maxX, maxY } = ctx.bounds;
          if (minX < -slack || minY < -slack || maxX > WIDTH + slack || maxY > HEIGHT + slack) {
            overflows.push(`${composition.id}/${state}/${locale}/f${frameIndex}`);
          }
        }
      }
    }
  }
  assert.deepEqual(overflows, [], `tràn khung: ${overflows.slice(0, 5).join(", ")}`);
  return `${frames} frame, không lỗi, không tràn`;
});

// ----------------------------------------------------------- 6. PNG -> clip

check("PNG sequence -> .vclip", () => {
  const workspace = mkdtempSync(join(tmpdir(), "veetee-uidemo-"));
  try {
    const frames = join(workspace, "png");
    mkdirSync(frames);
    for (let frame = 0; frame < 3; frame += 1) {
      writeFileSync(
        join(frames, `frame_${String(frame).padStart(3, "0")}.png`),
        // Nửa RGBA nửa RGB, luân phiên đủ 5 filter type của PNG.
        encodePng(480, 560, frame % 2 === 0 ? 6 : 2, (x, y) => {
          if ((x - 240) ** 2 + (y - 220 - frame * 8) ** 2 < 120 ** 2) return [0xc8, 0xf3, 0x6b, 255];
          if (y > 460) return [0xfb, 0xfb, 0xf7, 255];
          return [0, 0, 0, 0];
        }),
      );
    }
    const output = join(workspace, "out.vclip");
    execFileSync(
      process.execPath,
      [fileURLToPathCompat(resolve("tools/pack-clip.mjs")), "pack", frames, output, "--fps=12", "--background=#102C33"],
      { stdio: "pipe" },
    );
    const packed = readFileSync(output);
    const clip = reader.decodeClip(asBuffer(packed), "out.vclip");
    assert.equal(clip.width, WIDTH);
    assert.equal(clip.height, HEIGHT);
    assert.equal(clip.frameCount, 3);

    const image = reader.clipFrame(clip, 0);
    const at = (x, y) => {
      const index = (y * WIDTH + x) * 4;
      return codec.pack565(image.data[index], image.data[index + 1], image.data[index + 2]);
    };
    assert.equal(at(120, 110), codec.pack565(0xc8, 0xf3, 0x6b), "tâm phải là accent lime");
    assert.equal(at(3, 3), codec.pack565(0x10, 0x2c, 0x33), "vùng trong suốt phải ghép lên --background");
    assert.equal(at(120, 250), codec.pack565(0xfb, 0xfb, 0xf7), "nền dưới phải là foreground");
    execFileSync(process.execPath, [fileURLToPathCompat(resolve("tools/pack-clip.mjs")), "inspect", output], {
      stdio: "pipe",
    });
    return `resize cover + composite alpha + RLE, ${(packed.length / 3 / 1024).toFixed(1)} KiB/frame`;
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

// -------------------------------------------------------------- 7. HTML id

// app.js và capture-hiyori.js cần DOM nên self-check không import được; ít nhất
// bắt lỗi cú pháp để một dấu phẩy thiếu không lọt tới trình duyệt.
check("cú pháp module cần DOM", () => {
  const files = ["app.js", "tools/capture-hiyori.js"];
  for (const file of files) {
    // `node --check <file.js>` KHÔNG bắt được lỗi trong file .js dùng cú pháp
    // ESM — nó trả về 0. Phải ép parse dạng module qua stdin thì mới thật sự
    // kiểm. Đã thử lại bằng cách chèn lỗi cố ý để chắc nhóm này không vô dụng.
    execFileSync(process.execPath, ["--input-type=module", "--check"], {
      input: readFileSync(new URL(file, ROOT)),
      stdio: ["pipe", "pipe", "pipe"],
    });
  }
  return files.join(", ");
});

await checkAsync("import của module cần DOM có thật", async () => {
  const files = ["app.js", "tools/capture-hiyori.js"];
  let checked = 0;
  for (const file of files) {
    const source = readFileSync(new URL(file, ROOT), "utf8");
    for (const match of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']/g)) {
      const target = await import(new URL(match[2], new URL(file, ROOT)));
      const exported = new Set(Object.keys(target));
      for (const entry of match[1].split(",")) {
        const name = entry.trim().split(/\s+as\s+/)[0].trim();
        if (!name) continue;
        assert.ok(exported.has(name), `${file}: ${match[2]} không export "${name}"`);
        checked += 1;
      }
    }
  }
  return `${checked} named import đều tồn tại`;
});

check("id HTML khớp querySelector", () => {
  const pairs = [
    ["index.html", "app.js"],
    ["tools/capture-hiyori.html", "tools/capture-hiyori.js"],
  ];
  const lines = [];
  for (const [htmlPath, jsPath] of pairs) {
    const html = readFileSync(new URL(htmlPath, ROOT), "utf8");
    const js = readFileSync(new URL(jsPath, ROOT), "utf8");
    const declared = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]));
    const queried = [...js.matchAll(/querySelector\("#([^"]+)"\)/g)].map((match) => match[1]);
    const missing = queried.filter((id) => !declared.has(id));
    assert.deepEqual(missing, [], `${htmlPath} thiếu id: ${missing.join(", ")}`);
    lines.push(`${htmlPath} ${queried.length}/${declared.size}`);
  }
  return lines.join(", ");
});

// ------------------------------------------------------------------ report

let failed = 0;
for (const result of results) {
  if (result.error) {
    failed += 1;
    process.stdout.write(`FAIL  ${result.name}\n      ${result.error.message.split("\n")[0]}\n`);
  } else {
    process.stdout.write(`ok    ${result.name} — ${result.detail}\n`);
  }
}
process.stdout.write(failed === 0 ? `\n${results.length} nhóm kiểm tra đều đạt.\n` : `\n${failed} nhóm thất bại.\n`);
process.exitCode = failed === 0 ? 0 : 1;

// ------------------------------------------------------------------ helpers

function fileURLToPathCompat(url) {
  return decodeURIComponent(url.pathname.replace(/^\/([A-Za-z]:)/, "$1"));
}

// PNG encoder chỉ dùng cho test: xoay vòng 5 filter type để kiểm decoder.
function encodePng(width, height, colorType, pixelAt) {
  const channels = colorType === 6 ? 4 : 3;
  const stride = width * channels;
  const raw = Buffer.alloc(height * (stride + 1));
  const previous = Buffer.alloc(stride);
  for (let y = 0; y < height; y += 1) {
    const line = Buffer.alloc(stride);
    for (let x = 0; x < width; x += 1) {
      const [red, green, blue, alpha] = pixelAt(x, y);
      line[x * channels] = red;
      line[x * channels + 1] = green;
      line[x * channels + 2] = blue;
      if (channels === 4) line[x * channels + 3] = alpha;
    }
    const filter = y % 5;
    raw[y * (stride + 1)] = filter;
    for (let index = 0; index < stride; index += 1) {
      const left = index >= channels ? line[index - channels] : 0;
      const up = previous[index];
      const upLeft = index >= channels ? previous[index - channels] : 0;
      let value = line[index];
      if (filter === 1) value -= left;
      else if (filter === 2) value -= up;
      else if (filter === 3) value -= (left + up) >> 1;
      else if (filter === 4) value -= paethPredictor(left, up, upLeft);
      raw[y * (stride + 1) + 1 + index] = value & 0xff;
    }
    line.copy(previous);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = colorType;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", deflateSync(raw)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

function pngChunk(type, data) {
  const out = Buffer.alloc(12 + data.length);
  out.writeUInt32BE(data.length, 0);
  out.write(type, 4, "latin1");
  Buffer.from(data).copy(out, 8);
  out.writeUInt32BE(codec.crc32(out.subarray(4, 8 + data.length)), 8 + data.length);
  return out;
}

function paethPredictor(left, up, upLeft) {
  const estimate = left + up - upLeft;
  const distanceLeft = Math.abs(estimate - left);
  const distanceUp = Math.abs(estimate - up);
  const distanceUpLeft = Math.abs(estimate - upLeft);
  if (distanceLeft <= distanceUp && distanceLeft <= distanceUpLeft) return left;
  return distanceUp <= distanceUpLeft ? up : upLeft;
}
