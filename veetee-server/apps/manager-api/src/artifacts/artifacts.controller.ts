import {
  BadRequestException,
  Controller,
  Get,
  Headers,
  Param,
  Res,
  UnauthorizedException,
} from "@nestjs/common";
import type { FastifyReply } from "fastify";

import { Public } from "../auth/public.decorator.js";
import { ControlPlaneStore } from "../store/control-plane.store.js";
import {
  ArtifactFilesService,
  ArtifactRangeNotSatisfiableException,
  type ArtifactFileResponse,
} from "./artifact-files.service.js";

type RequestHeaders = Record<string, string | string[] | undefined>;

@Public()
@Controller()
export class ArtifactsController {
  constructor(
    private readonly store: ControlPlaneStore,
    private readonly artifacts: ArtifactFilesService,
  ) {}

  @Get("veetee/artifacts/manifests/:id")
  async manifest(
    @Param("id") id: string,
    @Headers() headers: RequestHeaders,
    @Res() reply: FastifyReply,
  ): Promise<void> {
    await this.authorize(id, headers);
    this.send(reply, await this.artifacts.openManifest(id));
  }

  @Get("veetee/artifacts/:id/content")
  async content(
    @Param("id") id: string,
    @Headers() headers: RequestHeaders,
    @Res() reply: FastifyReply,
  ): Promise<void> {
    await this.authorize(id, headers);
    try {
      this.send(reply, await this.artifacts.openContent(id, this.header(headers, "range")));
    } catch (error) {
      if (error instanceof ArtifactRangeNotSatisfiableException) {
        reply.header("Accept-Ranges", "bytes");
        reply.header("Content-Range", `bytes */${error.artifactSize}`);
      }
      throw error;
    }
  }

  private async authorize(id: string, headers: RequestHeaders): Promise<void> {
    const hardwareId = this.header(headers, "device-id");
    const authorization = this.header(headers, "authorization");
    if (!hardwareId || !/^[A-Za-z0-9][A-Za-z0-9:._-]{3,127}$/.test(hardwareId)) {
      throw new BadRequestException("Device-Id header is invalid");
    }
    if (!authorization?.startsWith("Bearer ")) {
      throw new UnauthorizedException("Device authorization is missing");
    }
    const token = authorization.slice(7);
    if (token.length < 32 || token.length > 256) {
      throw new UnauthorizedException("Device token is invalid");
    }
    const identity = await this.store.authenticateDeviceByHardware(hardwareId, token);
    const device = await this.store.deviceForAuthenticatedDevice(identity.deviceId);
    this.artifacts.assertDeviceAccess(id, device.desiredState.state);
  }

  private send(reply: FastifyReply, response: ArtifactFileResponse): void {
    reply.code(response.statusCode);
    for (const [name, value] of Object.entries(response.headers)) {
      reply.header(name, value);
    }
    reply.send(response.stream);
  }

  private header(headers: RequestHeaders, name: string): string | undefined {
    const value = headers[name.toLowerCase()];
    return Array.isArray(value) ? value[0] : value;
  }
}
