export type VoiceRuntimeComponent = "vad" | "asr" | "tts";

export interface VoiceRuntimeProbeResult {
  healthy: boolean;
  errorCode: string | null;
}

interface ReadyComponent {
  name?: unknown;
  healthy?: unknown;
  detail?: unknown;
}

export async function probeVoiceRuntimeComponent(
  componentName: VoiceRuntimeComponent,
  baseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<VoiceRuntimeProbeResult> {
  try {
    const response = await fetchImpl(`${baseUrl.replace(/\/$/, "")}/health/ready`, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(3_000),
    });
    const payload = (await response.json()) as { components?: ReadyComponent[] };
    const component = Array.isArray(payload.components)
      ? payload.components.find((item) => item.name === componentName)
      : undefined;

    if (!component) return { healthy: false, errorCode: "runtime_component_unreported" };
    if (component.healthy === true) return { healthy: true, errorCode: null };
    return { healthy: false, errorCode: "runtime_component_unhealthy" };
  } catch (error) {
    return {
      healthy: false,
      errorCode:
        error instanceof DOMException && error.name === "TimeoutError"
          ? "timeout"
          : "voice_runtime_unreachable",
    };
  }
}
