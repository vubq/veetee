import { Body, Controller, Get, Param, Post, Req } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import {
  ArrayMaxSize,
  ArrayUnique,
  IsArray,
  IsInt,
  IsOptional,
  IsString,
  IsUUID,
  Max,
  Min,
} from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  FirmwareRolloutService,
  type FirmwareReleaseRecord,
  type FirmwareRolloutRecord,
} from "./firmware-rollout.service.js";

class FirmwareRolloutDto {
  @IsString()
  artifactId!: string;

  @IsInt()
  @Min(0)
  @Max(100)
  percentage!: number;

  @IsArray()
  @ArrayUnique()
  @ArrayMaxSize(100)
  @IsUUID("4", { each: true })
  canaryDeviceIds!: string[];

  @IsOptional()
  @IsString()
  channel?: string;
}

class ResumeFirmwareRolloutDto {
  @IsOptional()
  @IsInt()
  @Min(0)
  @Max(100)
  percentage?: number;
}

@Controller("api/v1")
export class FirmwareRolloutController {
  constructor(private readonly firmware: FirmwareRolloutService) {}

  @Get("firmware-releases")
  async releases(@CurrentPrincipal() principal: Principal): Promise<FirmwareReleaseRecord[]> {
    return this.firmware.listReleases(principal.tenantId);
  }

  @Roles(TenantRole.ADMIN)
  @Post("firmware-releases/:id/publish")
  async publish(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<FirmwareReleaseRecord> {
    return this.firmware.publishRelease(id, { principal, requestId: request.id });
  }

  @Get("firmware-rollouts")
  async rollouts(@CurrentPrincipal() principal: Principal): Promise<FirmwareRolloutRecord[]> {
    return this.firmware.listRollouts(principal.tenantId);
  }

  @Roles(TenantRole.OPERATOR)
  @Post("firmware-rollouts")
  async create(
    @Body() input: FirmwareRolloutDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<FirmwareRolloutRecord> {
    return this.firmware.createRollout(input, { principal, requestId: request.id });
  }

  @Roles(TenantRole.OPERATOR)
  @Post("firmware-rollouts/:id/pause")
  async pause(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<FirmwareRolloutRecord> {
    return this.firmware.pause(id, { principal, requestId: request.id });
  }

  @Roles(TenantRole.OPERATOR)
  @Post("firmware-rollouts/:id/resume")
  async resume(
    @Param("id") id: string,
    @Body() input: ResumeFirmwareRolloutDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<FirmwareRolloutRecord> {
    return this.firmware.resume(id, input.percentage, {
      principal,
      requestId: request.id,
    });
  }

  @Roles(TenantRole.OPERATOR)
  @Post("firmware-rollouts/:id/rollback")
  async rollback(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<FirmwareRolloutRecord> {
    return this.firmware.rollback(id, { principal, requestId: request.id });
  }
}
