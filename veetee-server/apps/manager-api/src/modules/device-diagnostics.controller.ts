import {
  Body,
  Controller,
  Get,
  Logger,
  Param,
  Post,
  Req,
} from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { IsInt, Max, Min } from "class-validator";

import { AuditService } from "../audit/audit.service.js";
import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  DeviceDiagnosticsService,
  type AudioDiagnosticSession,
  type DeviceHealth,
  type DeviceSelfTest,
} from "../diagnostics/device-diagnostics.service.js";
import { ControlPlaneStore } from "../store/control-plane.store.js";

class StartAudioDiagnosticDto {
  @IsInt()
  @Min(1)
  @Max(30)
  durationSeconds!: number;
}

@Roles(TenantRole.OPERATOR)
@Controller("api/v1/devices/:id/diagnostics")
export class DeviceDiagnosticsController {
  private readonly logger = new Logger(DeviceDiagnosticsController.name);

  constructor(
    private readonly store: ControlPlaneStore,
    private readonly diagnostics: DeviceDiagnosticsService,
    private readonly audit: AuditService,
  ) {}

  @Get("health")
  async health(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
  ): Promise<DeviceHealth> {
    await this.store.device(principal.tenantId, id);
    return this.diagnostics.health(id);
  }

  @Post("audio-sessions")
  async startAudio(
    @Param("id") id: string,
    @Body() input: StartAudioDiagnosticDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<AudioDiagnosticSession> {
    await this.store.device(principal.tenantId, id);
    return this.runAudited(
      id,
      principal,
      request.id,
      "device.diagnostics.audio",
      { durationSeconds: input.durationSeconds, rawAudioStored: false },
      () => this.diagnostics.startAudio(id, input.durationSeconds),
    );
  }

  @Post("self-test")
  async selfTest(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<DeviceSelfTest> {
    await this.store.device(principal.tenantId, id);
    return this.runAudited(
      id,
      principal,
      request.id,
      "device.diagnostics.self_test",
      {},
      () => this.diagnostics.selfTest(id),
    );
  }

  private async runAudited<T>(
    deviceId: string,
    principal: Principal,
    requestId: string,
    actionPrefix: string,
    details: Record<string, unknown>,
    operation: () => Promise<T>,
  ): Promise<T> {
    await this.audit.record({
      tenantId: principal.tenantId,
      actorUserId: principal.userId,
      action: `${actionPrefix}.requested`,
      targetType: "device",
      targetId: deviceId,
      requestId,
      details,
    });
    try {
      const result = await operation();
      try {
        await this.audit.record({
          tenantId: principal.tenantId,
          actorUserId: principal.userId,
          action: `${actionPrefix}.succeeded`,
          targetType: "device",
          targetId: deviceId,
          requestId,
          after: result,
          details,
        });
      } catch (auditError) {
        this.logger.error(
          "Diagnostic completed but outcome audit failed",
          auditError instanceof Error ? auditError.stack : undefined,
        );
      }
      return result;
    } catch (error) {
      try {
        await this.audit.record({
          tenantId: principal.tenantId,
          actorUserId: principal.userId,
          action: `${actionPrefix}.failed`,
          targetType: "device",
          targetId: deviceId,
          requestId,
          details: {
            ...details,
            error: error instanceof Error ? error.constructor.name : "UnknownError",
          },
        });
      } catch (auditError) {
        this.logger.error(
          "Diagnostic failed and outcome audit also failed",
          auditError instanceof Error ? auditError.stack : undefined,
        );
      }
      throw error;
    }
  }
}
