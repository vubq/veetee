import { randomUUID } from "node:crypto";

import {
  BadRequestException,
  HttpException,
  HttpStatus,
  Injectable,
  NotFoundException,
  UnauthorizedException,
} from "@nestjs/common";
import { jwtVerify, SignJWT } from "jose";

import { AuditService } from "../audit/audit.service.js";
import type { Principal } from "../auth/auth.types.js";
import { PrismaService } from "../database/prisma.service.js";
import { RedisService } from "../database/redis.service.js";

export type LabInputMode = "text" | "audio_replay" | "live_mic";
export type LabMcpMode = "simulated" | "selected_device" | "disabled";

export interface CreateLabSessionInput {
  agentId: string;
  inputMode: LabInputMode;
  mcpMode: LabMcpMode;
  deviceId?: string;
}

interface LabClaims {
  sessionId: string;
  tenantId: string;
  userId: string;
  agentId: string;
  configVersion: number;
  inputMode: LabInputMode;
  mcpMode: LabMcpMode;
  deviceId?: string;
}

const TOKEN_SECONDS = 90;
const RATE_LIMIT_PER_MINUTE = 12;

@Injectable()
export class LabSessionService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly redis: RedisService,
    private readonly audit: AuditService,
  ) {}

  async create(
    input: CreateLabSessionInput,
    principal: Principal,
    requestId: string,
  ): Promise<Record<string, unknown>> {
    await this.enforceRateLimit(principal.tenantId, principal.userId);
    const agent = await this.prisma.agent.findFirst({
      where: { id: input.agentId, tenantId: principal.tenantId },
      select: {
        id: true,
        name: true,
        defaultLocale: true,
        interactionMode: true,
        publishedVersion: true,
      },
    });
    if (!agent) throw new NotFoundException("Agent not found");
    if (agent.publishedVersion <= 0) {
      throw new BadRequestException("Agent must have a published config before opening Lab");
    }
    if (input.mcpMode === "selected_device") {
      if (!input.deviceId) {
        throw new BadRequestException("deviceId is required for selected_device MCP mode");
      }
      const device = await this.prisma.device.findFirst({
        where: { id: input.deviceId, tenantId: principal.tenantId },
        select: { id: true },
      });
      if (!device) throw new NotFoundException("Device not found");
    } else if (input.deviceId) {
      throw new BadRequestException("deviceId is only accepted for selected_device MCP mode");
    }

    const sessionId = randomUUID();
    const claims: LabClaims = {
      sessionId,
      tenantId: principal.tenantId,
      userId: principal.userId,
      agentId: agent.id,
      configVersion: agent.publishedVersion,
      inputMode: input.inputMode,
      mcpMode: input.mcpMode,
      ...(input.deviceId ? { deviceId: input.deviceId } : {}),
    };
    const token = await new SignJWT({
      token_type: "lab_session",
      tenant_id: claims.tenantId,
      user_id: claims.userId,
      agent_id: claims.agentId,
      config_version: claims.configVersion,
      input_mode: claims.inputMode,
      mcp_mode: claims.mcpMode,
      ...(claims.deviceId ? { device_id: claims.deviceId } : {}),
    })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject(sessionId)
      .setJti(sessionId)
      .setIssuer("veetee-manager")
      .setAudience("veetee-voice-lab")
      .setIssuedAt()
      .setExpirationTime(`${TOKEN_SECONDS}s`)
      .sign(this.tokenKey());

    const stored = await this.redis.client.set(
      this.sessionKey(sessionId),
      JSON.stringify(claims),
      "EX",
      TOKEN_SECONDS,
      "NX",
    );
    if (stored !== "OK") throw new UnauthorizedException("Unable to issue Lab session");

    await this.audit.record({
      tenantId: principal.tenantId,
      actorUserId: principal.userId,
      action: "lab.session.create",
      targetType: "agent",
      targetId: agent.id,
      requestId,
      details: {
        sessionId,
        inputMode: input.inputMode,
        mcpMode: input.mcpMode,
        ...(input.deviceId ? { deviceId: input.deviceId } : {}),
      },
    });

    return {
      id: sessionId,
      token,
      websocketUrl: this.labWebsocketUrl(),
      expiresAt: new Date(Date.now() + TOKEN_SECONDS * 1_000).toISOString(),
      agent: {
        id: agent.id,
        name: agent.name,
        locale: agent.defaultLocale,
        version: agent.publishedVersion,
        interactionMode: agent.interactionMode.toLowerCase(),
      },
      inputMode: input.inputMode,
      mcpMode: input.mcpMode,
      ...(input.deviceId ? { deviceId: input.deviceId } : {}),
    };
  }

  async consume(token: string): Promise<LabClaims> {
    let payload: Awaited<ReturnType<typeof jwtVerify>>["payload"];
    try {
      payload = (
        await jwtVerify(token, this.tokenKey(), {
          issuer: "veetee-manager",
          audience: "veetee-voice-lab",
        })
      ).payload;
    } catch {
      throw new UnauthorizedException("Lab token is invalid or expired");
    }
    const sessionId = this.requiredString(payload.sub);
    if (
      payload.token_type !== "lab_session" ||
      payload.jti !== sessionId ||
      typeof payload.tenant_id !== "string" ||
      typeof payload.user_id !== "string" ||
      typeof payload.agent_id !== "string" ||
      !Number.isInteger(payload.config_version) ||
      !this.isInputMode(payload.input_mode) ||
      !this.isMcpMode(payload.mcp_mode) ||
      (payload.device_id !== undefined && typeof payload.device_id !== "string")
    ) {
      throw new UnauthorizedException("Lab token claims are invalid");
    }
    const stored = await this.redis.client.getdel(this.sessionKey(sessionId));
    if (!stored) throw new UnauthorizedException("Lab token was already used or expired");
    let claims: LabClaims;
    try {
      claims = JSON.parse(stored) as LabClaims;
    } catch {
      throw new UnauthorizedException("Lab session state is invalid");
    }
    if (
      claims.sessionId !== sessionId ||
      claims.tenantId !== payload.tenant_id ||
      claims.userId !== payload.user_id ||
      claims.agentId !== payload.agent_id ||
      claims.configVersion !== payload.config_version ||
      claims.inputMode !== payload.input_mode ||
      claims.mcpMode !== payload.mcp_mode ||
      claims.deviceId !== payload.device_id
    ) {
      throw new UnauthorizedException("Lab session state does not match token");
    }
    return claims;
  }

  private async enforceRateLimit(tenantId: string, userId: string): Promise<void> {
    const key = `veetee:lab-rate:${tenantId}:${userId}`;
    const count = await this.redis.client.incr(key);
    if (count === 1) await this.redis.client.expire(key, 60);
    if (count > RATE_LIMIT_PER_MINUTE) {
      throw new HttpException(
        "Too many Lab sessions; retry in one minute",
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }
  }

  private tokenKey(): Uint8Array {
    const secret = process.env.VEETEE_LAB_TOKEN_SECRET ?? process.env.VEETEE_AUTH_SECRET;
    if (!secret || secret.length < 32) {
      throw new Error("VEETEE_LAB_TOKEN_SECRET must contain at least 32 characters");
    }
    return new TextEncoder().encode(secret);
  }

  private labWebsocketUrl(): string {
    const explicit = process.env.VEETEE_VOICE_LAB_WS_URL;
    if (explicit) return explicit;
    const deviceUrl = process.env.VEETEE_VOICE_WS_URL ?? "ws://127.0.0.1:8000/veetee/v1/";
    const parsed = new URL(deviceUrl);
    parsed.pathname = "/veetee/lab/v1/";
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  }

  private sessionKey(sessionId: string): string {
    return `veetee:lab-session:${sessionId}`;
  }

  private requiredString(value: unknown): string {
    if (typeof value !== "string" || !value) {
      throw new UnauthorizedException("Lab token claims are invalid");
    }
    return value;
  }

  private isInputMode(value: unknown): value is LabInputMode {
    return value === "text" || value === "audio_replay" || value === "live_mic";
  }

  private isMcpMode(value: unknown): value is LabMcpMode {
    return value === "simulated" || value === "selected_device" || value === "disabled";
  }
}
