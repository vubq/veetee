export class ProviderDeadlineExceededError extends Error {
  public constructor(public readonly deadlineAtMs: number) {
    super("Provider operation deadline exceeded");
    this.name = "ProviderDeadlineExceededError";
  }
}

export class ProviderOperationCancelledError extends Error {
  public constructor(public readonly reason: unknown) {
    super("Provider operation cancelled", { cause: reason });
    this.name = "ProviderOperationCancelledError";
  }
}

export interface ProviderOperationContext {
  readonly turnId: string;
  readonly generation: number;
  readonly deadlineAtMs: number;
  readonly signal: AbortSignal;
  remainingMs(): number;
  throwIfCancelled(): void;
}

export interface ProviderOperationScope extends ProviderOperationContext {
  cancel(reason?: unknown): void;
  dispose(): void;
}

export function createProviderOperationScope(
  turnId: string,
  generation: number,
  timeoutMs: number,
  parentSignal?: AbortSignal,
): ProviderOperationScope {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new RangeError("timeoutMs must be positive and finite");
  }

  const controller = new AbortController();
  const deadlineAtMs = Date.now() + timeoutMs;
  const deadlineError = new ProviderDeadlineExceededError(deadlineAtMs);
  const timer = setTimeout(() => controller.abort(deadlineError), timeoutMs);

  const cancelFromParent = () => controller.abort(parentSignal?.reason);
  if (parentSignal?.aborted) {
    cancelFromParent();
  } else {
    parentSignal?.addEventListener("abort", cancelFromParent, { once: true });
  }

  return {
    turnId,
    generation,
    deadlineAtMs,
    signal: controller.signal,
    remainingMs: () => Math.max(0, deadlineAtMs - Date.now()),
    throwIfCancelled: () => {
      if (controller.signal.aborted) {
        if (controller.signal.reason instanceof ProviderDeadlineExceededError) {
          throw controller.signal.reason;
        }
        throw new ProviderOperationCancelledError(controller.signal.reason);
      }
      if (Date.now() >= deadlineAtMs) {
        throw deadlineError;
      }
    },
    cancel: (reason?: unknown) => controller.abort(reason),
    dispose: () => {
      clearTimeout(timer);
      parentSignal?.removeEventListener("abort", cancelFromParent);
    },
  };
}
