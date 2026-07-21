import {
  BadRequestException,
  Body,
  ConflictException,
  Controller,
  Headers,
  HttpCode,
  HttpStatus,
  Post,
  Res,
} from "@nestjs/common";
import type { FastifyReply } from "fastify";

import { Public } from "../auth/public.decorator.js";
import { ControlPlaneStore } from "../store/control-plane.store.js";

type RequestHeaders = Record<string, string | string[] | undefined>;

interface BootstrapResponse {
  server_time: { timestamp: number; timezone_offset: number };
  activation?: {
    code: string;
    message: string;
    challenge: string;
    expires_at: string;
    timeout_ms: number;
  };
  websocket: { url: string; token: string };
  firmware: { version: string; url: string };
  config?: { version: number; etag: string; url: string };
  resources?: { version: string; manifest_url: string };
}

@Public()
@Controller()
export class OtaController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Post("veetee/ota")
  @HttpCode(HttpStatus.OK)
  async bootstrap(
    @Headers() headers: RequestHeaders,
    @Body() body: Record<string, unknown> | undefined,
  ): Promise<BootstrapResponse> {
    const report = body ?? {};
    const hardwareId = this.identity(headers, "device-id", true);
    this.identity(headers, "client-id", false, hardwareId);
    const firmwareVersion = this.reportedValue(
      headers,
      "firmware-version",
      report,
      "application",
      "version",
    );
    this.reportedValue(headers, "device-model", report, "board", "type");
    this.validateLocale(this.header(headers, "accept-language"));
    const token = this.bearerToken(headers);
    const state = await this.store.bootstrapDevice(hardwareId, token, firmwareVersion);
    if (state.state === "pending_activation") {
      throw new ConflictException("Device pairing is complete and activation is pending");
    }

    const response: BootstrapResponse = {
      server_time: {
        timestamp: Date.now(),
        timezone_offset: -new Date().getTimezoneOffset(),
      },
      websocket: {
        url: this.voiceUrl(),
        token: state.state === "active" ? token ?? "" : "",
      },
      firmware: {
        version: process.env.VEETEE_FIRMWARE_VERSION ?? firmwareVersion ?? "0.1.0",
        url: process.env.VEETEE_FIRMWARE_URL ?? "",
      },
    };

    if (state.state === "unbound") {
      const template = process.env.VEETEE_ACTIVATION_MESSAGE_TEMPLATE ?? "{code}";
      response.activation = {
        code: state.activation.code,
        message: template.replaceAll("{code}", state.activation.code),
        challenge: state.activation.challenge,
        expires_at: state.activation.expiresAt,
        timeout_ms: Math.max(
          1_000,
          new Date(state.activation.expiresAt).getTime() - Date.now(),
        ),
      };
      return response;
    }

    const managerUrl = this.managerUrl();
    response.config = {
      version: state.configVersion,
      etag: `agent-config-${state.configVersion}`,
      url: `${managerUrl}/veetee/config/v1/devices/${encodeURIComponent(state.deviceId)}`,
    };
    const manifestUrl = state.resourceManifestId
      ? `${managerUrl}/veetee/artifacts/manifests/${encodeURIComponent(state.resourceManifestId)}`
      : process.env.VEETEE_RESOURCE_MANIFEST_URL;
    if (manifestUrl) {
      response.resources = {
        version: state.resourceVersion ?? process.env.VEETEE_RESOURCE_VERSION ?? "0.0.0",
        manifest_url: this.httpUrl(manifestUrl, "VEETEE_RESOURCE_MANIFEST_URL"),
      };
    }
    return response;
  }

  @Post("veetee/ota/activate")
  @HttpCode(HttpStatus.OK)
  async activate(
    @Headers() headers: RequestHeaders,
    @Body() body: Record<string, unknown> | undefined,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<Record<string, unknown>> {
    const payload = body ?? {};
    const hardwareId = this.identity(headers, "device-id", true);
    this.identity(headers, "client-id", false, hardwareId);
    if (typeof payload.hardwareId === "string" && payload.hardwareId !== hardwareId) {
      throw new BadRequestException("Body hardwareId must match Device-Id");
    }
    const challenge =
      typeof payload.challenge === "string"
        ? payload.challenge
        : this.header(headers, "activation-challenge");
    if (!challenge || challenge.length < 16 || challenge.length > 128) {
      reply.code(HttpStatus.ACCEPTED);
      return { status: "pending" };
    }

    const result = await this.store.activateDevice(hardwareId, challenge);
    if (!result) {
      reply.code(HttpStatus.ACCEPTED);
      return { status: "pending" };
    }
    return {
      status: "active",
      device_id: result.deviceId,
      agent_id: result.agentId,
      token: result.token,
      websocket_url: result.websocketUrl,
      config_version: result.configVersion,
    };
  }

  private identity(
    headers: RequestHeaders,
    name: string,
    required: boolean,
    fallback?: string,
  ): string {
    const value = this.header(headers, name) ?? fallback;
    if (!value) {
      if (required) throw new BadRequestException(`${name} header is required`);
      return "";
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9:._-]{3,127}$/.test(value)) {
      throw new BadRequestException(`${name} header is invalid`);
    }
    return value;
  }

  private reportedValue(
    headers: RequestHeaders,
    headerName: string,
    body: Record<string, unknown>,
    objectName: string,
    propertyName: string,
  ): string | undefined {
    const nested = body[objectName];
    const bodyValue =
      nested && typeof nested === "object"
        ? (nested as Record<string, unknown>)[propertyName]
        : undefined;
    const value = this.header(headers, headerName) ??
      (typeof bodyValue === "string" ? bodyValue : undefined);
    if (value && (value.length > 80 || !/^[A-Za-z0-9][A-Za-z0-9._+:/ -]*$/.test(value))) {
      throw new BadRequestException(`${headerName} is invalid`);
    }
    return value;
  }

  private validateLocale(value: string | undefined): void {
    if (value && !/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*(?:,[A-Za-z0-9;= ._-]+)*$/.test(value)) {
      throw new BadRequestException("Accept-Language is invalid");
    }
  }

  private bearerToken(headers: RequestHeaders): string | undefined {
    const authorization = this.header(headers, "authorization");
    if (!authorization) return undefined;
    if (!authorization.startsWith("Bearer ")) {
      throw new BadRequestException("Authorization must use Bearer authentication");
    }
    const token = authorization.slice(7);
    if (token.length < 32 || token.length > 256) {
      throw new BadRequestException("Device token is invalid");
    }
    return token;
  }

  private header(headers: RequestHeaders, name: string): string | undefined {
    const value = headers[name.toLowerCase()];
    return Array.isArray(value) ? value[0] : value;
  }

  private managerUrl(): string {
    return this.httpUrl(
      process.env.VEETEE_MANAGER_PUBLIC_URL ??
        process.env.VEETEE_MANAGER_API_URL ??
        "http://127.0.0.1:8001",
      "VEETEE_MANAGER_PUBLIC_URL",
    ).replace(/\/$/, "");
  }

  private voiceUrl(): string {
    const value = process.env.VEETEE_VOICE_WS_URL ?? "ws://127.0.0.1:8000/veetee/v1/";
    const url = new URL(value);
    if (!(["ws:", "wss:"] as string[]).includes(url.protocol)) {
      throw new Error("VEETEE_VOICE_WS_URL must use ws:// or wss://");
    }
    return url.toString();
  }

  private httpUrl(value: string, name: string): string {
    const url = new URL(value);
    if (!(["http:", "https:"] as string[]).includes(url.protocol)) {
      throw new Error(`${name} must use http:// or https://`);
    }
    return url.toString();
  }
}
