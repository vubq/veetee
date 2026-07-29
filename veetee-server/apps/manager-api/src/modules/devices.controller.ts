import {
  BadRequestException,
  Body,
  Controller,
  Get,
  Headers,
  HttpStatus,
  Param,
  Put,
  Req,
  Res,
  UseGuards,
} from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { Type } from "class-transformer";
import {
  ArrayUnique,
  IsArray,
  IsBoolean,
  IsIn,
  IsInt,
  IsObject,
  IsOptional,
  IsString,
  IsUUID,
  Matches,
  Max,
  MaxLength,
  Min,
  ValidateNested,
} from "class-validator";
import type { FastifyReply } from "fastify";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { DeviceAuthGuard } from "../auth/device-auth.guard.js";
import { Public } from "../auth/public.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  DeviceConfigService,
  matchesDeviceConfigEtag,
  type SignedDeviceConfigV1,
} from "../config/device-config.service.js";
import { ControlPlaneStore, type DeviceRecord } from "../store/control-plane.store.js";

class DesiredStateDto {
  @IsObject()
  state!: Record<string, unknown>;
}

export class AssignAgentDto {
  @IsOptional()
  @IsUUID("4")
  agentId?: string;
}

const resourcePhases = [
  "checking",
  "downloading",
  "verifying",
  "staged",
  "applying",
  "active",
  "failed",
  "rolled_back",
  "rebooting",
  "pending_health",
] as const;

const configPhases = ["checking", "applying", "active", "failed"] as const;

export class ReportedFirmwareStateDto {
  @IsString()
  @MaxLength(32)
  @Matches(/^[A-Za-z0-9][A-Za-z0-9.+_-]*$/)
  version!: string;
}

class ReportedDisplayCapabilityDto {
  @IsString()
  @MaxLength(64)
  @Matches(/^[a-z0-9][a-z0-9._-]*$/)
  target!: string;

  @IsString()
  @IsIn(["st7789"])
  controller!: string;

  @IsInt()
  @Min(80)
  @Max(320)
  width!: number;

  @IsInt()
  @Min(80)
  @Max(320)
  height!: number;

  @IsString()
  @IsIn(["rgb565"])
  colorFormat!: string;

  @IsInt()
  @Min(1)
  @Max(64)
  resourceAbi!: number;

  @IsInt()
  @Min(1)
  @Max(64)
  uiAbi!: number;

  @IsInt()
  @Min(1)
  @Max(16_777_216)
  slotBytes!: number;

  @IsBoolean()
  hotReload!: boolean;

  @IsArray()
  @ArrayUnique()
  @IsIn(["signal", "monolith", "quiet"], { each: true })
  compositions!: string[];
}

class ReportedWakeCapabilityDto {
  @IsString()
  @IsIn(["esp-sr"])
  runtime!: string;

  @IsInt()
  @Min(1)
  @Max(64)
  runtimeAbi!: number;

  @IsInt()
  @Min(1)
  @Max(64)
  resourceAbi!: number;

  @IsInt()
  @Min(1)
  @Max(16_777_216)
  slotBytes!: number;

  @IsInt()
  @Min(8_000)
  @Max(48_000)
  sampleRateHz!: number;

  @IsInt()
  @Min(1)
  @Max(2)
  channels!: number;

  @IsBoolean()
  hotReload!: boolean;
}

class ReportedCapabilitiesDto {
  @IsString()
  @MaxLength(64)
  @Matches(/^[a-z0-9][a-z0-9._-]*$/)
  board!: string;

  @IsObject()
  @ValidateNested()
  @Type(() => ReportedDisplayCapabilityDto)
  display!: ReportedDisplayCapabilityDto;

  @IsObject()
  @ValidateNested()
  @Type(() => ReportedWakeCapabilityDto)
  wake!: ReportedWakeCapabilityDto;
}

export class ReportedResourceStateDto {
  @IsIn(resourcePhases)
  phase!: (typeof resourcePhases)[number];

  @IsString()
  @MaxLength(32)
  @Matches(/^[A-Za-z0-9][A-Za-z0-9.+_-]*$/)
  currentVersion!: string;

  @IsString()
  @MaxLength(32)
  @Matches(/^[A-Za-z0-9][A-Za-z0-9.+_-]*$/)
  desiredVersion!: string;

  @IsInt()
  @Min(0)
  @Max(1)
  activeSlot!: number;

  @IsInt()
  @Min(0)
  @Max(1)
  targetSlot!: number;

  @IsInt()
  @Min(0)
  @Max(16_777_216)
  expectedBytes!: number;

  @IsInt()
  @Min(0)
  @Max(16_777_216)
  downloadedBytes!: number;

  @IsInt()
  @Min(0)
  @Max(2_147_483_647)
  securityEpoch!: number;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  @Matches(/^[a-z0-9][a-z0-9._-]*$/)
  errorCode?: string;
}

export class ReportedConfigStateDto {
  @IsInt()
  @Min(1)
  @Max(2_147_483_647)
  desiredVersion!: number;

  @IsInt()
  @Min(0)
  @Max(2_147_483_647)
  appliedVersion!: number;

  @IsIn(configPhases)
  phase!: (typeof configPhases)[number];

  @IsOptional()
  @IsString()
  @MaxLength(32)
  @Matches(/^[a-z0-9][a-z0-9._-]*$/)
  errorCode?: string;
}

export class ReportedDeviceStateDto {
  @IsInt()
  @Min(1)
  @Max(1)
  schemaVersion!: number;

  @IsOptional()
  @IsString()
  @MaxLength(35)
  @Matches(/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/)
  locale?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  @Matches(/^[A-Za-z0-9._+-]+(?:\/[A-Za-z0-9._+-]+)*$/)
  timeZone?: string;

  @IsObject()
  @ValidateNested()
  @Type(() => ReportedFirmwareStateDto)
  firmware!: ReportedFirmwareStateDto;

  @IsOptional()
  @IsObject()
  @ValidateNested()
  @Type(() => ReportedCapabilitiesDto)
  capabilities?: ReportedCapabilitiesDto;

  @IsOptional()
  @IsObject()
  @ValidateNested()
  @Type(() => ReportedResourceStateDto)
  resource?: ReportedResourceStateDto;

  @IsOptional()
  @IsObject()
  @ValidateNested()
  @Type(() => ReportedResourceStateDto)
  ui?: ReportedResourceStateDto;

  @IsOptional()
  @IsObject()
  @ValidateNested()
  @Type(() => ReportedResourceStateDto)
  firmware_ota?: ReportedResourceStateDto;

  @IsOptional()
  @IsObject()
  @ValidateNested()
  @Type(() => ReportedConfigStateDto)
  config?: ReportedConfigStateDto;
}

export class ReportedStateDto {
  @IsInt()
  @Min(1)
  @Max(2_147_483_647)
  version!: number;

  @IsObject()
  @ValidateNested()
  @Type(() => ReportedDeviceStateDto)
  state!: ReportedDeviceStateDto;

  @IsUUID("4")
  bootId!: string;
}

@Controller()
export class DevicesController {
  constructor(
    private readonly store: ControlPlaneStore,
    private readonly deviceConfig: DeviceConfigService,
  ) {}

  @Get("api/v1/devices")
  async list(@CurrentPrincipal() principal: Principal): Promise<DeviceRecord[]> {
    return this.store.listDevices(principal.tenantId);
  }

  @Public()
  @UseGuards(DeviceAuthGuard)
  @Get("veetee/config/v1/devices/:id")
  async desired(
    @Param("id") id: string,
    @Headers("if-none-match") ifNoneMatch: string | string[] | undefined,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<SignedDeviceConfigV1 | undefined> {
    const config = await this.deviceConfig.snapshot(id);
    reply.header("ETag", `"${config.etag}"`);
    reply.header("Cache-Control", "private, no-cache");
    if (matchesDeviceConfigEtag(ifNoneMatch, config.etag)) {
      reply.code(HttpStatus.NOT_MODIFIED);
      return undefined;
    }
    return config.body;
  }

  @Roles(TenantRole.OPERATOR)
  @Put("api/v1/devices/:id/desired-state")
  async setDesired(
    @Param("id") id: string,
    @Body() input: DesiredStateDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<DeviceRecord> {
    return this.store.setDesiredState(id, input.state, { principal, requestId: request.id });
  }

  @Roles(TenantRole.OPERATOR)
  @Put("api/v1/devices/:id/agent")
  async assignAgent(
    @Param("id") id: string,
    @Body() input: AssignAgentDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<DeviceRecord> {
    return this.store.assignDeviceAgent(id, input.agentId, {
      principal,
      requestId: request.id,
    });
  }

  @Public()
  @UseGuards(DeviceAuthGuard)
  @Put("veetee/devices/:id/reported-state")
  async report(@Param("id") id: string, @Body() input: ReportedStateDto): Promise<DeviceRecord> {
    const artifacts = [input.state.resource, input.state.ui, input.state.firmware_ota].filter(
      (artifact): artifact is ReportedResourceStateDto => artifact != null,
    );
    const subsystemCount = artifacts.length + (input.state.config ? 1 : 0);
    if (subsystemCount !== 1) {
      throw new BadRequestException("Reported state must contain exactly one reconcile subsystem");
    }
    const config = input.state.config;
    if (config) {
      if (config.appliedVersion > config.desiredVersion) {
        throw new BadRequestException("Reported config appliedVersion exceeds desiredVersion");
      }
      if (config.phase === "active" && config.appliedVersion !== config.desiredVersion) {
        throw new BadRequestException("Reported active config versions do not match");
      }
      if ((config.phase === "failed") !== Boolean(config.errorCode)) {
        throw new BadRequestException("Reported config failure state has an invalid errorCode");
      }
    } else {
      const artifact = artifacts[0]!;
      if (artifact.downloadedBytes > artifact.expectedBytes) {
        throw new BadRequestException("Reported downloadedBytes exceeds expectedBytes");
      }
      const failure = ["failed", "rolled_back"].includes(artifact.phase);
      if (failure !== Boolean(artifact.errorCode)) {
        throw new BadRequestException("Reported artifact failure state has an invalid errorCode");
      }
    }
    return this.store.updateReportedState(
      id,
      input.version,
      input.state as unknown as Record<string, unknown>,
      input.bootId,
    );
  }
}
