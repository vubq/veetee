import {
  BadRequestException,
  Body,
  Controller,
  Get,
  Param,
  Put,
  Req,
  UseGuards,
} from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { Type } from "class-transformer";
import {
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

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { DeviceAuthGuard } from "../auth/device-auth.guard.js";
import { Public } from "../auth/public.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import { ControlPlaneStore, type DeviceRecord } from "../store/control-plane.store.js";

class DesiredStateDto {
  @IsObject()
  state!: Record<string, unknown>;
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
] as const;

export class ReportedFirmwareStateDto {
  @IsString()
  @MaxLength(32)
  @Matches(/^[A-Za-z0-9][A-Za-z0-9.+_-]*$/)
  version!: string;
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

export class ReportedDeviceStateDto {
  @IsInt()
  @Min(1)
  @Max(1)
  schemaVersion!: number;

  @IsObject()
  @ValidateNested()
  @Type(() => ReportedFirmwareStateDto)
  firmware!: ReportedFirmwareStateDto;

  @IsObject()
  @ValidateNested()
  @Type(() => ReportedResourceStateDto)
  resource!: ReportedResourceStateDto;
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
  constructor(private readonly store: ControlPlaneStore) {}

  @Get("api/v1/devices")
  async list(@CurrentPrincipal() principal: Principal): Promise<DeviceRecord[]> {
    return this.store.listDevices(principal.tenantId);
  }

  @Public()
  @UseGuards(DeviceAuthGuard)
  @Get("veetee/config/v1/devices/:id")
  async desired(@Param("id") id: string): Promise<DeviceRecord["desiredState"]> {
    const device = await this.store.deviceForAuthenticatedDevice(id);
    return device.desiredState;
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

  @Public()
  @UseGuards(DeviceAuthGuard)
  @Put("veetee/devices/:id/reported-state")
  async report(@Param("id") id: string, @Body() input: ReportedStateDto): Promise<DeviceRecord> {
    if (input.state.resource.downloadedBytes > input.state.resource.expectedBytes) {
      throw new BadRequestException("Reported downloadedBytes exceeds expectedBytes");
    }
    const failure = ["failed", "rolled_back"].includes(input.state.resource.phase);
    if (failure !== Boolean(input.state.resource.errorCode)) {
      throw new BadRequestException("Reported resource failure state has an invalid errorCode");
    }
    return this.store.updateReportedState(
      id,
      input.version,
      input.state as unknown as Record<string, unknown>,
      input.bootId,
    );
  }
}
