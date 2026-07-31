// Live2D -> VTCLIP1 capture, run on a PC once, never on the device.
//
// Hiyori is posed deterministically per frame from a loop phase so the exported
// clip loops seamlessly at the device frame rate. Nothing here is a runtime
// dependency of the demo: it only produces assets/hiyori/*.vclip.

import {
  CLIP_FLAG_DELTA,
  encodeFrame,
  encodeFrameSequence,
  pack565,
  rgbaToPanelPixels,
  writeClip,
  crc32,
} from "../src/clip-codec.js";
import {
  BUDGET_HEADROOM,
  UI_SLOT_PRESETS,
  costModel,
  frameCountFor,
  planWithinBudget,
} from "../src/clip-budget.js";

const PANEL = { width: 240, height: 280 };
const BACKGROUND = [0x10, 0x2c, 0x33];
const BACKGROUND_PACKED = pack565(BACKGROUND[0], BACKGROUND[1], BACKGROUND[2]);
const MOUTH_LEVELS = 4;

// Nguồn chính thức: Live2D/CubismWebSamples, thư mục Samples/Resources/Hiyori.
// Model tham chiếu Hiyori.moc3, Hiyori.2048/texture_0{0,1}.png, physics3/pose3/
// userdata3/cdi3 và các motion Idle[0..8] + TapBody[0]. Muốn chạy hoàn toàn
// offline thì tải thư mục Hiyori về ./assets/Hiyori/ rồi đổi hằng số này.
const MODEL_URL =
  "https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Hiyori/Hiyori.model3.json";

// Only the head-to-waist section fits a 240x280 portrait panel.
const MODEL_VIEW = { centerX: 0.5, top: 20, visibleHeight: 258, upperBodyFraction: 0.38 };

// Độ dài vòng lặp tính bằng giây là lựa chọn nghệ thuật; số frame là hệ quả của
// fps, và fps là hệ quả của ngân sách (xem src/clip-budget.js). Không đặt số
// frame cứng ở đây, vì đó là thứ ngân sách mới có quyền quyết định.
const CLIPS = [
  { id: "boot", seconds: 1.0 },
  { id: "idle", seconds: 2.0 },
  { id: "listening", seconds: 1.35 },
  { id: "thinking", seconds: 1.7 },
  { id: "speaking", seconds: 1.35 },
  { id: "closing", seconds: 1.0 },
];

const STATE_MAP = {
  starting: "boot",
  wifi_configuring: "boot",
  network_connecting: "boot",
  activating: "idle",
  pairing_recovery: "idle",
  idle: "idle",
  connecting: "idle",
  listening: "listening",
  evaluating: "thinking",
  thinking: "thinking",
  speaking: "speaking",
  aborting: "idle",
  closing: "closing",
};

const dom = {
  start: document.querySelector("#start"),
  slotSize: document.querySelector("#slot-size"),
  fps: document.querySelector("#fps"),
  scale: document.querySelector("#frame-scale"),
  mouthRect: document.querySelector("#mouth-rect"),
  log: document.querySelector("#log"),
  preview: document.querySelector("#preview"),
  status: document.querySelector("#status"),
};

const scratch = document.createElement("canvas");
scratch.width = PANEL.width;
scratch.height = PANEL.height;
const scratchCtx = scratch.getContext("2d", { willReadFrequently: true });

let app = null;
let model = null;
let pose = poseFor("idle", 0);

boot();

async function boot() {
  try {
    if (!window.PIXI?.live2d?.Live2DModel) throw new Error("Runtime pixi-live2d-display chưa tải");
    app = new PIXI.Application({
      width: PANEL.width,
      height: PANEL.height,
      backgroundColor: (BACKGROUND[0] << 16) | (BACKGROUND[1] << 8) | BACKGROUND[2],
      backgroundAlpha: 1,
      antialias: true,
      autoStart: false,
      resolution: 1,
      preserveDrawingBuffer: true,
    });
    document.querySelector("#viewport").append(app.view);

    // autoUpdate off: the shared ticker must not advance the model behind our
    // back, or two captures of the same phase would differ.
    model = await PIXI.live2d.Live2DModel.from(MODEL_URL, { autoUpdate: false, autoInteract: false });
    app.stage.addChild(model);
    fitModel();
    model.internalModel.motionManager.stopAllMotions();

    // Cubism4InternalModel.update() runs, in order:
    //   beforeMotionUpdate -> motion -> afterMotionUpdate -> saveParameters
    //   -> expression -> eyeBlink -> focus -> naturalMovements -> physics
    //   -> pose -> beforeModelUpdate -> coreModel.update()
    // Only beforeModelUpdate is after every automatic writer, so it is the one
    // hook where a pose actually survives to the rendered frame. Writing at
    // afterMotionUpdate would let eyeBlink and naturalMovements overwrite it.
    model.internalModel.on("beforeModelUpdate", applyPose);

    // internalModel.update() is skipped when deltaTime is 0, so seed a real step.
    render(1000 / 12);
    setStatus("Model sẵn sàng. Chọn fps rồi bấm Xuất clip.", "ready");
    dom.start.disabled = false;
  } catch (error) {
    console.error(error);
    setStatus(`Không tải được model: ${error.message}`, "error");
  }
}

function fitModel() {
  const baseHeight = model.height / (Math.abs(model.scale.y) || 1);
  model.anchor.set(0.5, 1);
  const scale = MODEL_VIEW.visibleHeight / (baseHeight * MODEL_VIEW.upperBodyFraction);
  model.scale.set(scale);
  model.position.set(PANEL.width * MODEL_VIEW.centerX, MODEL_VIEW.top + baseHeight * scale);
}

function applyPose() {
  const core = model.internalModel.coreModel;
  const set = (id, value) => {
    try {
      core.setParameterValueById(id, value);
    } catch {
      // A sample model may not expose every parameter; the capture still runs.
    }
  };
  set("ParamAngleX", pose.angleX);
  set("ParamAngleY", pose.angleY);
  set("ParamAngleZ", pose.angleZ);
  set("ParamBodyAngleX", pose.bodyX);
  set("ParamEyeBallX", pose.eyeX);
  set("ParamEyeBallY", pose.eyeY);
  set("ParamEyeLOpen", pose.eyeOpen);
  set("ParamEyeROpen", pose.eyeOpen);
  set("ParamMouthOpenY", pose.mouthOpen);
  set("ParamMouthForm", pose.mouthForm);
  set("ParamBreath", pose.breath);
}

function render(deltaMs) {
  model.update(deltaMs);
  app.render();
}

function capturePanel() {
  scratchCtx.drawImage(app.view, 0, 0, PANEL.width, PANEL.height);
  return scratchCtx.getImageData(0, 0, PANEL.width, PANEL.height);
}

function poseFor(id, phase) {
  const wave = Math.sin(phase * Math.PI * 2);
  const fast = Math.sin(phase * Math.PI * 4);
  const base = {
    angleX: 0,
    angleY: 0,
    angleZ: 0,
    bodyX: 0,
    eyeX: 0,
    eyeY: 0,
    eyeOpen: 1,
    mouthOpen: 0,
    mouthForm: 0,
    breath: (wave + 1) / 2,
  };
  switch (id) {
    case "boot":
      return { ...base, angleY: -6 + wave * 2, angleZ: wave * 2, eyeOpen: 0.3 + 0.3 * ((wave + 1) / 2) };
    case "listening":
      return { ...base, angleX: 4 + wave * 2, angleY: -2, eyeY: 0.15, eyeOpen: blink(phase, 0.9) };
    case "thinking":
      return { ...base, angleX: -12 + wave * 3, angleY: 7, angleZ: -4 + fast, eyeX: 0.4, eyeY: 0.55, eyeOpen: 0.85 };
    case "speaking":
      // Miệng để đóng trong clip nền. Chuyển động miệng nằm hoàn toàn ở overlay
      // và do biên độ TTS chọn, nếu không thì hai nguồn sẽ đánh nhau và miệng
      // mấp máy theo nhịp cố định bất kể thiết bị đang nói gì.
      return { ...base, angleX: 1 + wave * 2, bodyX: 1.4 + wave, eyeOpen: 0.95, mouthOpen: 0 };
    case "closing":
      return { ...base, angleY: -8, angleZ: -3, mouthForm: 0.3, eyeOpen: Math.max(0.02, 1 - phase * 1.6) };
    default:
      return { ...base, angleX: wave * 3, angleZ: fast * 1.2, bodyX: wave * 0.9, eyeOpen: blink(phase, 0.82) };
  }
}

function blink(phase, at, width = 0.07) {
  const distance = Math.min(Math.abs(phase - at), Math.abs(phase - at + 1), Math.abs(phase - at - 1));
  return distance > width ? 1 : Math.max(0.02, distance / width);
}

async function captureClip(id, frameCount, fps, mouthRect) {
  const frames = [];
  const mouthByFrame = [];
  const step = 1000 / fps;
  const wantsMouth = Boolean(mouthRect) && id === "speaking";

  for (let index = 0; index < frameCount; index += 1) {
    const basePose = poseFor(id, index / frameCount);
    pose = basePose;
    render(step);
    const image = capturePanel();
    frames.push(rgbaToPanelPixels(image.data, PANEL.width * PANEL.height, BACKGROUND));
    dom.preview.getContext("2d").putImageData(image, 0, 0);

    if (wantsMouth) {
      // Đầu vẫn cử động suốt clip speaking, nên một khung miệng chụp ở đúng một
      // pose sẽ lệch ở mọi frame khác. Vì vậy chụp đủ 4 mức miệng cho TỪNG frame
      // nền, ngay tại pose của frame đó.
      const levels = [];
      for (let level = 0; level < MOUTH_LEVELS; level += 1) {
        pose = { ...basePose, mouthOpen: level / (MOUTH_LEVELS - 1) };
        // dt xấp xỉ 0: pose mới được áp nhưng physics chưa kịp trôi, nên khung
        // miệng khớp tuyệt đối với frame nền vừa chụp. dt = 0 thì _render bỏ qua
        // cả internalModel.update() nên pose sẽ không được áp.
        render(0.0001);
        const patch = scratchCtxFrom(mouthRect);
        levels.push(rgbaToPanelPixels(patch.data, mouthRect.width * mouthRect.height, BACKGROUND));
      }
      mouthByFrame.push(levels);
    }
    if (index % 4 === 0) await nextTick();
  }
  return { id, frames, fps, rect: contentBounds(frames), mouthByFrame, mouthRect: wantsMouth ? mouthRect : null };
}

// Mọi pixel ngoài bóng nhân vật đúng bằng màu nền phẳng, mà renderer đã tô sẵn
// trước khi blit. Cắt theo bóng nên không mất gì về hình ảnh, lại giảm cả dung
// lượng clip lẫn số byte phải đẩy qua SPI mỗi frame.
function contentBounds(frames) {
  let minX = PANEL.width;
  let minY = PANEL.height;
  let maxX = -1;
  let maxY = -1;
  for (const pixels of frames) {
    for (let y = 0; y < PANEL.height; y += 1) {
      const row = y * PANEL.width;
      for (let x = 0; x < PANEL.width; x += 1) {
        if (pixels[row + x] === BACKGROUND_PACKED) continue;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return { x: 0, y: 0, width: PANEL.width, height: PANEL.height };
  return { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
}

function cropPixels(pixels, rect) {
  const out = new Uint16Array(rect.width * rect.height);
  for (let y = 0; y < rect.height; y += 1) {
    const from = (rect.y + y) * PANEL.width + rect.x;
    out.set(pixels.subarray(from, from + rect.width), y * rect.width);
  }
  return out;
}

function encodeCapture(capture) {
  const { rect } = capture;
  const cropped = capture.frames.map((pixels) => cropPixels(pixels, rect));
  const base = writeClip(encodeFrameSequence(cropped), {
    width: rect.width,
    height: rect.height,
    fps: capture.fps,
    flags: cropped.length > 1 ? CLIP_FLAG_DELTA : 0,
  });
  if (capture.mouthByFrame.length === 0) return { base, mouth: null };

  // Phẳng hoá theo thứ tự frame nền rồi tới mức miệng, để runtime tra bằng
  // baseIndex * MOUTH_LEVELS + level.
  //
  // Overlay KHÔNG delta: biên độ TTS nhảy tự do nên đây là truy cập ngẫu nhiên,
  // mà chuỗi delta thì phải giải mã tuần tự từ keyframe. Overlay bé (khung miệng
  // vài nghìn pixel) nên giữ frame độc lập gần như không tốn thêm gì, đổi lại
  // firmware tra một phát ra ngay.
  const mouthPixels = capture.mouthByFrame.flat();
  const mouth = writeClip(mouthPixels.map(encodeFrame), {
    width: capture.mouthRect.width,
    height: capture.mouthRect.height,
    fps: capture.fps,
  });
  return { base, mouth };
}

function captureBytes(entry) {
  return entry.base.length + (entry.mouth?.length ?? 0);
}

// Nếu khung miệng đặt sai chỗ thì 4 mức sẽ giống hệt nhau và lip-sync im lặng
// không chạy. Bắt lỗi đó ngay lúc chụp thay vì để nó thành một lỗi hình ảnh.
function mouthLevelsDiffer(capture) {
  const first = capture.mouthByFrame[0];
  if (!first) return true;
  const closed = first[0];
  const open = first[first.length - 1];
  for (let index = 0; index < closed.length; index += 1) {
    if (closed[index] !== open[index]) return true;
  }
  return false;
}

function budgetSamples(captures, encoded) {
  return captures.map((capture) => ({
    id: capture.id,
    frames: capture.frames.length,
    base: encoded.get(capture.id).base,
    mouth: encoded.get(capture.id).mouth,
  }));
}

function scratchCtxFrom(rect) {
  scratchCtx.drawImage(app.view, 0, 0, PANEL.width, PANEL.height);
  return scratchCtx.getImageData(rect.x, rect.y, rect.width, rect.height);
}

async function run() {
  dom.start.disabled = true;
  dom.log.textContent = "";
  const fps = Number(dom.fps.value);
  const scale = Number(dom.scale.value);
  const files = [];
  let total = 0;

  try {
    const manual = parseRect(dom.mouthRect.value);
    const rect = manual ?? detectMouthRect();
    if (!rect) {
      setStatus("Không dò được vùng miệng — model không phản ứng với ParamMouthOpenY.", "error");
      dom.start.disabled = false;
      return;
    }
    log(
      manual
        ? `Vùng miệng (nhập tay): ${rect.width}×${rect.height} @ (${rect.x},${rect.y})`
        : `Vùng miệng (tự dò): ${rect.width}×${rect.height} @ (${rect.x},${rect.y})`,
    );

    const slotBytes = Number(dom.slotSize.value);
    const budget = Math.round(slotBytes * BUDGET_HEADROOM);
    log(`Ngân sách clip: ${formatBytes(budget)} trong UI slot ${formatBytes(slotBytes)}`);

    const captureAll = async (planFps, planScale) => {
      const captures = [];
      const encoded = new Map();
      for (const clip of CLIPS) {
        const frameCount = frameCountFor(clip, planFps, planScale);
        setStatus(`Đang chụp ${clip.id} (${frameCount} frame @ ${planFps} fps)…`);
        const capture = await captureClip(clip.id, frameCount, planFps, rect);
        captures.push(capture);
        encoded.set(capture.id, encodeCapture(capture));
      }
      return { captures, encoded };
    };

    let { captures, encoded } = await captureAll(fps, scale);
    const measured = captures.reduce((sum, capture) => sum + captureBytes(encoded.get(capture.id)), 0);

    // Đo xong mới biết chi phí thật của nội dung này, nên dùng số đo đó để chọn
    // fps thay vì bắt người dùng thử lại cho tới khi may mắn vừa.
    const model = costModel(budgetSamples(captures, encoded));
    const plan = planWithinBudget(CLIPS, model, fps, scale, budget);
    if (plan.fps !== fps || plan.scale !== scale) {
      log(
        `Ở ${fps} fps bộ clip nặng ${formatBytes(measured)}, quá ngân sách. ` +
          `Chụp lại ở ${plan.fps} fps${plan.scale !== scale ? `, vòng lặp ngắn lại ${plan.scale / scale}×` : ""}.`,
      );
      ({ captures, encoded } = await captureAll(plan.fps, plan.scale));
    }

    for (const capture of captures) {
      const crop = capture.rect;
      log(
        `${capture.id}.vclip · ${capture.frames.length} frame @ ${capture.fps} fps · ` +
          `crop ${crop.width}×${crop.height} @ (${crop.x},${crop.y}) · ` +
          `${formatBytes(captureBytes(encoded.get(capture.id)))}`,
      );
      if (capture.mouthByFrame.length > 0 && !mouthLevelsDiffer(capture)) {
        log(`Cảnh báo: 4 mức miệng giống hệt nhau — khung miệng chưa trùng miệng.`);
      }
    }

    for (const capture of captures) {
      const entry = encoded.get(capture.id);
      files.push({ name: `${capture.id}.vclip`, bytes: entry.base });
      total += entry.base.length;
      if (!entry.mouth) continue;
      files.push({ name: "mouth.vclip", bytes: entry.mouth });
      total += entry.mouth.length;
      log(
        `mouth.vclip · ${capture.frames.length} frame × ${MOUTH_LEVELS} mức · ` +
          `${rect.width}×${rect.height} @ (${rect.x},${rect.y}) · ${formatBytes(entry.mouth.length)}`,
      );
    }

    const manifest = buildManifest(captures, rect);
    files.push({ name: "manifest.json", bytes: new TextEncoder().encode(JSON.stringify(manifest, null, 2)) });

    const percent = ((total / slotBytes) * 100).toFixed(1);
    log(`Tổng ${formatBytes(total)} · ${percent}% của UI slot ${formatBytes(slotBytes)}`);

    download("hiyori-clips.zip", zipStore(files));
    setStatus(
      `Xong ở ${captures[0].fps} fps. Giải nén vào assets/hiyori/ rồi bấm “Nạp assets/hiyori” trong demo.`,
      "ready",
    );
  } catch (error) {
    console.error(error);
    setStatus(`Lỗi khi chụp: ${error.message}`, "error");
  }
  dom.start.disabled = false;
}

function buildManifest(captures, rect) {
  const clips = {};
  for (const capture of captures) {
    clips[capture.id] = {
      file: `${capture.id}.vclip`,
      fps: capture.fps,
      x: capture.rect.x,
      y: capture.rect.y,
      loop: true,
    };
  }
  const manifest = {
    schema_version: 1,
    kind: "ui_clip_set",
    character: "hiyori",
    source: "Live2D Cubism sample data — Hiyori Momose",
    target: { board: "veetee-s3-n16r8", display: "st7789-240x280-rgb565" },
    clips,
    state_map: STATE_MAP,
  };
  const speaking = captures.find((capture) => capture.mouthByFrame.length > 0);
  if (rect && speaking) {
    manifest.overlays = {
      mouth: {
        file: "mouth.vclip",
        x: rect.x,
        y: rect.y,
        levels: MOUTH_LEVELS,
        // Overlay được chụp cho từng frame nền, nên runtime phải tra bằng
        // baseIndex * levels + level, không phải chỉ level.
        per_frame: true,
        base: speaking.id,
      },
    };
  }
  return manifest;
}

// Đo khung miệng thay vì đoán: render cùng một pose ở miệng đóng và miệng mở
// hết, những pixel khác nhau chính là vùng miệng. Nới ra vài pixel cho bóng đổ
// và cạnh anti-alias.
function detectMouthRect(padding = 6) {
  let minX = PANEL.width;
  let minY = PANEL.height;
  let maxX = -1;
  let maxY = -1;

  // Đầu cử động suốt clip, nên lấy hợp của nhiều pha chứ không chỉ pha 0 —
  // nếu không, khung sẽ vừa ở đầu clip và hụt ở giữa clip.
  for (const phase of [0, 0.25, 0.5, 0.75]) {
    const basePose = poseFor("speaking", phase);
    pose = { ...basePose, mouthOpen: 0 };
    render(1000 / 12);
    const closed = rgbaToPanelPixels(capturePanel().data, PANEL.width * PANEL.height, BACKGROUND);
    pose = { ...basePose, mouthOpen: 1 };
    render(0.0001);
    const open = rgbaToPanelPixels(capturePanel().data, PANEL.width * PANEL.height, BACKGROUND);

    for (let y = 0; y < PANEL.height; y += 1) {
      const row = y * PANEL.width;
      for (let x = 0; x < PANEL.width; x += 1) {
        if (closed[row + x] === open[row + x]) continue;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return null;

  const x = Math.max(0, minX - padding);
  const y = Math.max(0, minY - padding);
  return {
    x,
    y,
    width: Math.min(PANEL.width, maxX + padding + 1) - x,
    height: Math.min(PANEL.height, maxY + padding + 1) - y,
  };
}

function parseRect(value) {
  const parts = value.split(",").map((part) => Number(part.trim()));
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0)) return null;
  const [x, y, width, height] = parts;
  if (width < 1 || height < 1 || x + width > PANEL.width || y + height > PANEL.height) return null;
  return { x, y, width, height };
}

// Store-only ZIP so the whole clip set arrives as one download.
function zipStore(files) {
  const encoder = new TextEncoder();
  const locals = [];
  const central = [];
  let offset = 0;

  for (const file of files) {
    const name = encoder.encode(file.name);
    const checksum = crc32(file.bytes);
    const local = new Uint8Array(30 + name.length + file.bytes.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, 0x04034b50, true);
    localView.setUint16(4, 20, true);
    localView.setUint16(8, 0, true);
    localView.setUint16(12, 0x21, true);
    localView.setUint32(14, checksum, true);
    localView.setUint32(18, file.bytes.length, true);
    localView.setUint32(22, file.bytes.length, true);
    localView.setUint16(26, name.length, true);
    local.set(name, 30);
    local.set(file.bytes, 30 + name.length);
    locals.push(local);

    const entry = new Uint8Array(46 + name.length);
    const entryView = new DataView(entry.buffer);
    entryView.setUint32(0, 0x02014b50, true);
    entryView.setUint16(4, 20, true);
    entryView.setUint16(6, 20, true);
    entryView.setUint16(14, 0x21, true);
    entryView.setUint32(16, checksum, true);
    entryView.setUint32(20, file.bytes.length, true);
    entryView.setUint32(24, file.bytes.length, true);
    entryView.setUint16(28, name.length, true);
    entryView.setUint32(42, offset, true);
    entry.set(name, 46);
    central.push(entry);

    offset += local.length;
  }

  const centralSize = central.reduce((total, entry) => total + entry.length, 0);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(8, files.length, true);
  endView.setUint16(10, files.length, true);
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, offset, true);

  const output = new Uint8Array(offset + centralSize + end.length);
  let cursor = 0;
  for (const chunk of [...locals, ...central, end]) {
    output.set(chunk, cursor);
    cursor += chunk.length;
  }
  return output;
}

function download(name, bytes) {
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function nextTick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function log(line) {
  dom.log.textContent += `${line}\n`;
}

function setStatus(message, variant = "") {
  dom.status.textContent = message;
  dom.status.className = `status ${variant}`.trim();
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}

dom.slotSize.replaceChildren(
  ...UI_SLOT_PRESETS.map((preset) => {
    const option = document.createElement("option");
    option.value = String(preset.bytes);
    option.textContent = preset.label;
    return option;
  }),
);
dom.start.addEventListener("click", () => void run());
