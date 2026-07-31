// Demo shell. Owns the control surface and the frame clock; all device pixels
// come from src/screens/* drawing into the 240x280 Panel.

import {
  COMPOSITIONS,
  PALETTE_SOURCES,
  PANEL,
  STATE_IDS,
  SCREEN_COPY,
  compositionById,
  copyFor,
  tokensFor,
} from "./src/contract.js";
import { Panel } from "./src/panel.js";
import { hydrateClipSet, loadClipSet, slotSizeFor, UI_SLOT_BYTES } from "./src/clip.js";
import { renderOs } from "./src/screens/os.js";
import { renderCompanion } from "./src/screens/companion.js";
import { renderEyes } from "./src/screens/eyes.js";

const RENDERERS = { signal: renderOs, monolith: renderCompanion, quiet: renderEyes };

// Regions the renderer actually has to re-flush per animation frame. Used for
// the SPI budget readout; a full frame is 240*280*2 = 134400 bytes.
//
// Giữ danh sách này trung thực với những gì màn hình thật sự làm động. Thêm
// animation thì vùng bẩn phải nở ra theo, nếu không ô ngân sách sẽ nói dối về
// giá phải trả.
const DIRTY_REGIONS = {
  // Chấm chữ ký thở, và dải tín hiệu. Tiêu đề không động nên không phải flush.
  signal: [
    [26, 68, 14, 12],
    [20, 194, 200, 28],
  ],
  monolith: [[0, 0, 240, 212]],
  // Hai mắt (đã cộng biên độ liếc và mí) và miệng. Bỏ khung mặt nên cũng bỏ
  // luôn hai vòng quầng sáng, vốn là phần ngốn SPI nhất của màn này.
  quiet: [
    [48, 82, 68, 96],
    [124, 82, 68, 96],
    [96, 182, 48, 28],
  ],
};

const view = {
  compositionId: "signal",
  state: "idle",
  palette: "web",
  locale: "ascii",
  activationCode: "284716",
  level: 0.45,
  autoLevel: true,
  frameStepMs: 33,
  rgb565: true,
  dither: false,
  grid: false,
  zoom: 2,
  spiClock: 10_000_000,
  clipSet: null,
};

const dom = {
  canvas: document.querySelector("#panel"),
  compositionList: document.querySelector("#composition-list"),
  stateList: document.querySelector("#state-list"),
  captionTitle: document.querySelector("#caption-title"),
  captionNote: document.querySelector("#caption-note"),
  captionState: document.querySelector("#caption-state"),
  budgetFull: document.querySelector("#budget-full"),
  budgetDirty: document.querySelector("#budget-dirty"),
  budgetFps: document.querySelector("#budget-fps"),
  budgetClip: document.querySelector("#budget-clip"),
  palette: document.querySelector("#palette"),
  locale: document.querySelector("#locale"),
  activationCode: document.querySelector("#activation-code"),
  level: document.querySelector("#level"),
  levelOutput: document.querySelector("#level-output"),
  autoLevel: document.querySelector("#auto-level"),
  rgb565: document.querySelector("#rgb565"),
  dither: document.querySelector("#dither"),
  grid: document.querySelector("#grid"),
  zoom: document.querySelector("#zoom"),
  frameModel: document.querySelector("#frame-model"),
  spiClock: document.querySelector("#spi-clock"),
  loadClips: document.querySelector("#load-clips"),
  clipFiles: document.querySelector("#clip-files"),
  clipStatus: document.querySelector("#clip-status"),
};

const panel = new Panel();
let lastFrameIndex = -1;
let dirty = true;

function buildCompositionList() {
  dom.compositionList.replaceChildren(
    ...COMPOSITIONS.map((composition) => {
      const button = document.createElement("button");
      button.type = "button";
      button.role = "radio";
      button.dataset.id = composition.id;
      button.className = "composition";
      button.innerHTML =
        `<span class="composition-index">${composition.index}</span>` +
        `<span class="composition-body"><strong>${composition.demoName}</strong>` +
        `<small>${composition.product} · <code>${composition.id}</code></small>` +
        `<small class="composition-note">${composition.note}</small></span>`;
      button.addEventListener("click", () => {
        view.compositionId = composition.id;
        invalidate();
      });
      return button;
    }),
  );
}

function buildStateList() {
  dom.stateList.replaceChildren(
    ...STATE_IDS.map((state) => {
      const button = document.createElement("button");
      button.type = "button";
      button.role = "radio";
      button.dataset.state = state;
      button.className = "state";
      button.innerHTML = `<span class="state-number">${SCREEN_COPY[state].number}</span>${SCREEN_COPY[state].label}`;
      button.addEventListener("click", () => {
        view.state = state;
        invalidate();
      });
      return button;
    }),
  );
}

function syncSelection() {
  for (const button of dom.compositionList.children) {
    const selected = button.dataset.id === view.compositionId;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-checked", String(selected));
  }
  for (const button of dom.stateList.children) {
    const selected = button.dataset.state === view.state;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-checked", String(selected));
  }
  const composition = compositionById(view.compositionId);
  dom.captionTitle.textContent = `${composition.index} · ${composition.product}`;
  dom.captionNote.textContent = composition.demoName;
  dom.captionState.textContent = `${view.state} · ${SCREEN_COPY[view.state].number}`;
}

function syncBudget() {
  const fullBytes = PANEL.width * PANEL.height * 2;
  const dirtyPixels = (DIRTY_REGIONS[view.compositionId] ?? []).reduce(
    (total, [, , width, height]) => total + width * height,
    0,
  );
  const dirtyBytes = dirtyPixels * 2;
  const fullSeconds = (fullBytes * 8) / view.spiClock;
  const dirtySeconds = (dirtyBytes * 8) / view.spiClock;

  dom.budgetFull.textContent = `${formatBytes(fullBytes)} · ${(fullSeconds * 1000).toFixed(1)} ms`;
  dom.budgetDirty.textContent = `${formatBytes(dirtyBytes)} · ${(dirtySeconds * 1000).toFixed(1)} ms`;
  dom.budgetFps.textContent = `${Math.floor(1 / fullSeconds)} fps toàn khung · ${Math.floor(1 / dirtySeconds)} fps vùng bẩn`;

  if (!view.clipSet) {
    dom.budgetClip.textContent = "—";
    return;
  }
  const { totalBytes } = view.clipSet;
  const percent = ((totalBytes / UI_SLOT_BYTES) * 100).toFixed(1);
  const needed = slotSizeFor(totalBytes);
  dom.budgetClip.textContent =
    needed === 2
      ? `${formatBytes(totalBytes)} · ${percent}% slot 2 MiB`
      : `${formatBytes(totalBytes)} · cần ui_0/ui_1 ${needed} MiB (nay 2 MiB)`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}

function invalidate() {
  dirty = true;
  syncSelection();
  syncBudget();
}

function envelope(elapsed) {
  if (!view.autoLevel) return view.level;
  if (view.state === "speaking") {
    const carrier =
      Math.sin(elapsed * 10.7) * 0.28 + Math.sin(elapsed * 17.2 + 1.7) * 0.18 + Math.sin(elapsed * 4.3 + 0.3) * 0.12;
    return Math.max(0.04, Math.min(1, 0.46 + carrier));
  }
  if (view.state === "listening") {
    const carrier = Math.sin(elapsed * 5.1) * 0.22 + Math.sin(elapsed * 9.4 + 0.9) * 0.14;
    return Math.max(0.05, Math.min(1, 0.42 + carrier));
  }
  return view.level;
}

function draw(elapsedSeconds) {
  const composition = compositionById(view.compositionId);
  const tokens = tokensFor(composition, view.state, view.palette);
  const step = view.frameStepMs / 1000;
  const frameIndex = Math.floor(elapsedSeconds / step);
  const time = frameIndex * step;

  panel.begin(tokens.background);
  RENDERERS[composition.id](panel.ctx, {
    composition,
    tokens,
    state: view.state,
    copy: copyFor(composition, view.state, view.locale),
    locale: view.locale,
    activationCode: view.activationCode,
    time,
    frameIndex,
    level: envelope(time),
    clipSet: view.clipSet,
  });
  if (view.rgb565) panel.quantize({ dither: view.dither });
  panel.present(dom.canvas, { scale: view.zoom, grid: view.grid });
}

function loop(timestamp) {
  const elapsedSeconds = timestamp / 1000;
  const frameIndex = Math.floor(elapsedSeconds / (view.frameStepMs / 1000));
  if (dirty || frameIndex !== lastFrameIndex) {
    lastFrameIndex = frameIndex;
    dirty = false;
    draw(elapsedSeconds);
  }
  requestAnimationFrame(loop);
}

async function adoptClipSet(promise, sourceLabel) {
  dom.clipStatus.textContent = `Đang nạp ${sourceLabel}…`;
  dom.clipStatus.classList.remove("is-error", "is-ready");
  try {
    const set = await promise;
    view.clipSet = set;
    const clipCount = Object.keys(set.clips).length;
    const overlayCount = Object.keys(set.overlays).length;
    dom.clipStatus.textContent =
      `Đã nạp ${clipCount} clip + ${overlayCount} overlay từ ${sourceLabel} · ${formatBytes(set.totalBytes)}`;
    dom.clipStatus.classList.add("is-ready");
    view.compositionId = "monolith";
  } catch (error) {
    view.clipSet = null;
    dom.clipStatus.textContent = `Không nạp được: ${error.message}`;
    dom.clipStatus.classList.add("is-error");
  }
  invalidate();
}

function bind() {
  dom.palette.replaceChildren(
    ...PALETTE_SOURCES.map((source) => {
      const option = document.createElement("option");
      option.value = source.id;
      option.textContent = source.label;
      return option;
    }),
  );
  dom.palette.value = view.palette;
  dom.palette.addEventListener("change", (event) => {
    view.palette = event.target.value;
    invalidate();
  });
  dom.locale.addEventListener("change", (event) => {
    view.locale = event.target.value;
    invalidate();
  });
  dom.activationCode.addEventListener("input", (event) => {
    const digits = event.target.value.replace(/\D/g, "").slice(0, 6);
    event.target.value = digits;
    view.activationCode = digits.padEnd(6, "0");
    invalidate();
  });
  dom.level.addEventListener("input", (event) => {
    view.level = Number(event.target.value) / 100;
    dom.levelOutput.textContent = `${event.target.value}%`;
    invalidate();
  });
  dom.autoLevel.addEventListener("change", (event) => {
    view.autoLevel = event.target.checked;
    invalidate();
  });
  dom.rgb565.addEventListener("change", (event) => {
    view.rgb565 = event.target.checked;
    dom.dither.disabled = !event.target.checked;
    invalidate();
  });
  dom.dither.addEventListener("change", (event) => {
    view.dither = event.target.checked;
    invalidate();
  });
  dom.grid.addEventListener("change", (event) => {
    view.grid = event.target.checked;
    invalidate();
  });
  dom.zoom.addEventListener("change", (event) => {
    view.zoom = Number(event.target.value);
    invalidate();
  });
  dom.frameModel.addEventListener("change", (event) => {
    view.frameStepMs = Number(event.target.value);
    invalidate();
  });
  dom.spiClock.addEventListener("change", (event) => {
    view.spiClock = Number(event.target.value);
    invalidate();
  });
  dom.loadClips.addEventListener("click", () => {
    void adoptClipSet(loadClipSet(new URL("./assets/hiyori/", window.location.href)), "assets/hiyori");
  });
  dom.clipFiles.addEventListener("change", async (event) => {
    const files = [...event.target.files];
    if (files.length === 0) return;
    const manifestFile = files.find((file) => file.name === "manifest.json");
    if (!manifestFile) {
      dom.clipStatus.textContent = "Cần chọn cả manifest.json cùng các file .vclip.";
      dom.clipStatus.classList.add("is-error");
      return;
    }
    const byName = new Map(files.map((file) => [file.name, file]));
    const manifest = JSON.parse(await manifestFile.text());
    await adoptClipSet(
      hydrateClipSet(manifest, async (name) => {
        const file = byName.get(name.split("/").pop());
        if (!file) throw new Error(`thiếu ${name}`);
        return file.arrayBuffer();
      }),
      "file cục bộ",
    );
    event.target.value = "";
  });
}

buildCompositionList();
buildStateList();
bind();
invalidate();
requestAnimationFrame(loop);
