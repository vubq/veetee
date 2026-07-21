import { createHash, randomInt, randomUUID } from "node:crypto";

import { BadRequestException, Injectable, NotFoundException } from "@nestjs/common";

export interface DeviceRecord {
  id: string;
  hardwareId: string;
  name: string;
  status: "online" | "idle" | "offline";
  agentId?: string;
  firmwareVersion?: string;
  desiredState: Record<string, unknown>;
  reportedState: Record<string, unknown>;
  pairedAt: string;
}

export interface AgentRecord {
  id: string;
  name: string;
  defaultLocale: string;
  interactionMode: "auto" | "manual" | "realtime";
  persona: string;
  version: number;
  publishedVersion: number;
}

export interface ProviderRecord {
  id: string;
  kind: "vad" | "asr" | "llm" | "tts" | "realtime";
  adapter: string;
  model: string;
  baseUrl?: string;
  secretConfigured: boolean;
  enabled: boolean;
  health: "unknown" | "healthy" | "degraded";
}

interface PairingTicket {
  digest: string;
  hardwareId: string;
  expiresAt: number;
  attemptsLeft: number;
  consumed: boolean;
}

@Injectable()
export class ControlPlaneStore {
  private readonly pairing = new Map<string, PairingTicket>();
  private readonly devices = new Map<string, DeviceRecord>();
  private readonly agents = new Map<string, AgentRecord>();
  private readonly providers = new Map<string, ProviderRecord>();

  constructor() {
    const agent: AgentRecord = {
      id: "agent-veetee-vi",
      name: "Veetee Việt",
      defaultLocale: "vi-VN",
      interactionMode: "auto",
      persona: "Robot AI thân thiện, rõ ràng và ưu tiên tiếng Việt.",
      version: 1,
      publishedVersion: 1,
    };
    this.agents.set(agent.id, agent);
    for (const provider of [
      this.provider("vad", "silero-local", "silero_vad"),
      this.provider("asr", "sherpa-onnx", "zipformer-vi-30m-int8"),
      this.provider("llm", "openai-compatible-9router", "cx/gpt-5.4-mini"),
      this.provider("tts", "vieneu-local", "vieneu-tts-v3-turbo"),
    ]) {
      provider.health = "healthy";
      this.providers.set(provider.id, provider);
    }
  }

  createPairingCode(hardwareId: string, ttlSeconds = 600): { code: string; expiresAt: string } {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      const code = randomInt(0, 1_000_000).toString().padStart(6, "0");
      const digest = this.digestCode(code);
      if (this.pairing.has(digest)) continue;
      const expiresAt = Date.now() + ttlSeconds * 1_000;
      this.pairing.set(digest, {
        digest,
        hardwareId,
        expiresAt,
        attemptsLeft: 5,
        consumed: false,
      });
      return { code, expiresAt: new Date(expiresAt).toISOString() };
    }
    throw new BadRequestException("Unable to allocate a pairing code");
  }

  claimPairing(code: string, name: string, agentId?: string): DeviceRecord {
    const digest = this.digestCode(code);
    const ticket = this.pairing.get(digest);
    if (!ticket || ticket.consumed || ticket.expiresAt <= Date.now()) {
      throw new BadRequestException("Pairing code is invalid or expired");
    }
    ticket.attemptsLeft -= 1;
    if (ticket.attemptsLeft < 0) {
      ticket.consumed = true;
      throw new BadRequestException("Pairing code attempt limit exceeded");
    }
    if (agentId && !this.agents.has(agentId)) throw new NotFoundException("Agent not found");
    ticket.consumed = true;
    const device: DeviceRecord = {
      id: randomUUID(),
      hardwareId: ticket.hardwareId,
      name,
      status: "idle",
      ...(agentId ? { agentId } : {}),
      desiredState: {},
      reportedState: {},
      pairedAt: new Date().toISOString(),
    };
    this.devices.set(device.id, device);
    return device;
  }

  listDevices(): DeviceRecord[] {
    return [...this.devices.values()];
  }

  device(id: string): DeviceRecord {
    const device = this.devices.get(id);
    if (!device) throw new NotFoundException("Device not found");
    return device;
  }

  updateReportedState(id: string, state: Record<string, unknown>): DeviceRecord {
    const device = this.device(id);
    device.reportedState = structuredClone(state);
    device.status = "online";
    return device;
  }

  setDesiredState(id: string, state: Record<string, unknown>): DeviceRecord {
    const device = this.device(id);
    device.desiredState = structuredClone(state);
    return device;
  }

  listAgents(): AgentRecord[] {
    return [...this.agents.values()];
  }

  createAgent(input: Omit<AgentRecord, "id" | "version" | "publishedVersion">): AgentRecord {
    const agent: AgentRecord = {
      ...input,
      id: randomUUID(),
      version: 1,
      publishedVersion: 0,
    };
    this.agents.set(agent.id, agent);
    return agent;
  }

  publishAgent(id: string): AgentRecord {
    const agent = this.agents.get(id);
    if (!agent) throw new NotFoundException("Agent not found");
    agent.version += 1;
    agent.publishedVersion = agent.version;
    return agent;
  }

  listProviders(): ProviderRecord[] {
    return [...this.providers.values()];
  }

  createProvider(input: Omit<ProviderRecord, "id" | "health">): ProviderRecord {
    const provider = { ...input, id: randomUUID(), health: "unknown" as const };
    this.providers.set(provider.id, provider);
    return provider;
  }

  testProvider(id: string): ProviderRecord {
    const provider = this.providers.get(id);
    if (!provider) throw new NotFoundException("Provider not found");
    provider.health = provider.enabled ? "healthy" : "degraded";
    return provider;
  }

  private provider(kind: ProviderRecord["kind"], adapter: string, model: string): ProviderRecord {
    return {
      id: `provider-${kind}`,
      kind,
      adapter,
      model,
      secretConfigured: kind !== "llm",
      enabled: true,
      health: "unknown",
    };
  }

  private digestCode(code: string): string {
    return createHash("sha256").update(code).digest("hex");
  }
}
