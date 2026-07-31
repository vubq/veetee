// Logic ngân sách clip, tách khỏi tool capture để kiểm được mà không cần DOM.
//
// Ngân sách là ràng buộc cứng, nên nó quyết định fps — không phải để người dùng
// chọn một con số rồi mới báo là không vừa. Ở đây chỉ có hàm thuần: đo chi phí
// thật từ một lần chụp, rồi suy ra dung lượng ở fps bất kỳ mà không phải chụp lại.

import { CLIP_HEADER_BYTES } from "./clip-codec.js";

export const MIN_FRAMES = 4;

// fps thử theo thứ tự giảm dần; chọn mức cao nhất còn vừa ngân sách.
export const FPS_LADDER = [15, 12, 10, 8, 6, 5, 4];

// Chừa chỗ cho manifest, theme.json và strings/ cũng nằm trong cùng UI Pack.
export const BUDGET_HEADROOM = 0.9;

// 2 MiB đến từ ui_0/ui_1 trong veetee-firmware/partitions/veetee_16mb.csv, mà
// dòng đầu file đó ghi rõ: "Provisional N16R8 layout. Freeze only after ESP-SR,
// Opus, TLS and UI size probes." Con số này đang chờ đúng phép đo này chứ chưa
// chốt, nên cho chọn được thay vì hard-code một mình mốc 2 MiB.
export const UI_SLOT_PRESETS = [
  { bytes: 2 * 1024 * 1024, label: "2 MiB · ui_0/ui_1 như hiện tại" },
  { bytes: 3 * 1024 * 1024, label: "3 MiB · phải thu ota_0/ota_1" },
  { bytes: 4 * 1024 * 1024, label: "4 MiB · phải đổi bảng phân vùng" },
];

// Chẵn để pha luôn cách đều, và tối thiểu MIN_FRAMES để vòng lặp còn ý nghĩa.
export function frameCountFor(clip, fps, scale) {
  return Math.max(MIN_FRAMES, Math.round((clip.seconds * scale * fps) / 2) * 2);
}

// Đọc độ dài một frame từ bảng offset trong container, không giải nén.
export function frameByteLength(clipBytes, frameIndex) {
  const view = new DataView(clipBytes.buffer, clipBytes.byteOffset, clipBytes.byteLength);
  const frameCount = view.getUint16(12, true);
  if (frameIndex < 0 || frameIndex >= frameCount) throw new RangeError("frameIndex ngoài khoảng");
  const payloadLength = view.getUint32(20, true);
  const start = view.getUint32(CLIP_HEADER_BYTES + frameIndex * 4, true);
  const end =
    frameIndex + 1 < frameCount ? view.getUint32(CLIP_HEADER_BYTES + (frameIndex + 1) * 4, true) : payloadLength;
  return end - start;
}

export function payloadLengthOf(clipBytes) {
  const view = new DataView(clipBytes.buffer, clipBytes.byteOffset, clipBytes.byteLength);
  return view.getUint32(20, true);
}

// Từ một lần chụp thật, rút ra chi phí keyframe và chi phí trung bình mỗi frame
// delta. `samples` là [{ id, frames, base, mouth }] với base/mouth là byte đã đóng gói.
//
// Tính trên payload chứ không trên độ dài cả file: header 32 byte và bảng offset
// 4 byte/frame là chi phí container, phải cộng riêng theo số frame ở
// estimateBytes, nếu không sẽ bị tính lẫn vào chi phí mỗi frame delta.
export function costModel(samples) {
  const model = new Map();
  for (const sample of samples) {
    const payload = payloadLengthOf(sample.base);
    const keyBytes = frameByteLength(sample.base, 0);
    const deltaBytes = sample.frames > 1 ? (payload - keyBytes) / (sample.frames - 1) : 0;
    const mouthPerFrame = sample.mouth ? sample.mouth.length / sample.frames : 0;
    model.set(sample.id, { keyBytes, deltaBytes, mouthPerFrame });
  }
  return model;
}

export function estimateBytes(clips, model, fps, scale) {
  let total = 0;
  for (const clip of clips) {
    const cost = model.get(clip.id);
    if (!cost) continue;
    const frames = frameCountFor(clip, fps, scale);
    total += CLIP_HEADER_BYTES + frames * 4;
    total += cost.keyBytes + (frames - 1) * cost.deltaBytes + frames * cost.mouthPerFrame;
  }
  // Manifest và header zip là vài KiB, làm tròn lên cho chắc.
  return total + 8 * 1024;
}

// Chọn fps cao nhất còn vừa ngân sách; chỉ khi tới đáy thang fps mà vẫn không
// vừa thì mới rút ngắn vòng lặp.
export function planWithinBudget(clips, model, requestedFps, requestedScale, budget) {
  for (let scale = requestedScale; scale >= 0.25; scale /= 2) {
    for (const fps of FPS_LADDER) {
      if (fps > requestedFps) continue;
      if (estimateBytes(clips, model, fps, scale) <= budget) return { fps, scale };
    }
  }
  return { fps: FPS_LADDER[FPS_LADDER.length - 1], scale: 0.25 };
}
