import { describe, expect, it, vi } from "vitest";

import {
  createProviderOperationScope,
  ProviderDeadlineExceededError,
  ProviderOperationCancelledError,
} from "./context.js";
import type { LlmProvider } from "./providers.js";
import { ProviderRegistry } from "./registry.js";

const fakeLlm: LlmProvider = {
  capabilities: {
    providerId: "fake-llm",
    kind: "llm",
    locales: ["vi-VN"],
    streamingInput: false,
    streamingOutput: true,
    cancellation: "cooperative",
    toolCalling: true,
    structuredOutput: true,
  },
  health: async () => ({ healthy: true }),
  async *generate(_request, context) {
    context.throwIfCancelled();
    yield { type: "text_delta", text: "Xin chao" };
    context.throwIfCancelled();
    yield { type: "done", finishReason: "stop" };
  },
};

describe("provider operation scope", () => {
  it("propagates parent cancellation through every provider call", () => {
    const parent = new AbortController();
    const scope = createProviderOperationScope("turn-1", 7, 1_000, parent.signal);
    parent.abort("button_interrupt");
    expect(() => scope.throwIfCancelled()).toThrow(ProviderOperationCancelledError);
    scope.dispose();
  });

  it("enforces an independent operation deadline", () => {
    vi.useFakeTimers();
    const scope = createProviderOperationScope("turn-2", 8, 25);
    vi.advanceTimersByTime(25);
    expect(() => scope.throwIfCancelled()).toThrow(ProviderDeadlineExceededError);
    scope.dispose();
    vi.useRealTimers();
  });
});

describe("provider registry", () => {
  it("resolves providers by capability kind instead of central vendor branches", () => {
    const registry = new ProviderRegistry();
    registry.register(fakeLlm);
    expect(registry.resolve<LlmProvider>("llm", "fake-llm")).toBe(fakeLlm);
    expect(registry.capabilities()).toHaveLength(1);
  });

  it("rejects ambiguous duplicate bindings", () => {
    const registry = new ProviderRegistry();
    registry.register(fakeLlm);
    expect(() => registry.register(fakeLlm)).toThrow(/already registered/);
  });
});
