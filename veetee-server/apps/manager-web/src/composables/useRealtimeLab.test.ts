import { describe, expect, it, vi } from "vitest";

import { unlockAudioContext } from "./useRealtimeLab";

type AudioState = AudioContextState | "interrupted";

function audioContext(initialState: AudioState, finalState: AudioState = "running") {
  const calls: string[] = [];
  const source = {
    buffer: null as AudioBuffer | null,
    connect: vi.fn(() => calls.push("connect")),
    disconnect: vi.fn(() => calls.push("disconnect")),
    onended: null as (() => void) | null,
    start: vi.fn(() => {
      calls.push("start");
      source.onended?.();
    }),
  };
  const context = {
    state: initialState,
    sampleRate: 48_000,
    destination: {},
    createBuffer: vi.fn(() => ({}) as AudioBuffer),
    createBufferSource: vi.fn(() => source),
    resume: vi.fn(async () => {
      calls.push("resume");
      context.state = finalState;
    }),
  };
  return { context: context as unknown as AudioContext, source, calls };
}

describe("unlockAudioContext", () => {
  it("starts a silent source before awaiting resume for mobile user activation", async () => {
    const { context, source, calls } = audioContext("suspended");

    await unlockAudioContext(context);

    expect(calls.slice(0, 3)).toEqual(["connect", "start", "disconnect"]);
    expect(calls.indexOf("start")).toBeLessThan(calls.indexOf("resume"));
    expect(source.buffer).not.toBeNull();
  });

  it("reports when a mobile browser still blocks audio", async () => {
    const { context } = audioContext("suspended", "suspended");

    await expect(unlockAudioContext(context)).rejects.toThrow(
      "Trình duyệt chưa cho phép phát âm thanh",
    );
  });
});
