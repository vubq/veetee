import { Body, Controller, Get, Param, Patch, Post, Req } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { Type } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  ArrayUnique,
  IsArray,
  IsIn,
  IsInt,
  IsLocale,
  IsNumber,
  IsOptional,
  IsString,
  IsUUID,
  Length,
  Matches,
  Max,
  Min,
  ValidateNested,
} from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  ResourceCatalogService,
  type ArtifactRecord,
  type ResourceRolloutRecord,
  type WakeProfileRecord,
} from "./resource-catalog.service.js";

const safeArtifactId = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const safeDetectorId = /^[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}$/;
const wakeStates = ["standby", "listening", "thinking", "speaking", "closing"];

class RegisterArtifactDto {
  @IsString()
  @Matches(safeArtifactId)
  artifactId!: string;

  @IsString()
  @Length(1, 120)
  license!: string;

  @IsOptional()
  @IsIn(["not_run", "passed", "failed"])
  benchmarkStatus: ArtifactRecord["benchmarkStatus"] = "not_run";
}

class ArtifactBenchmarkDto {
  @IsIn(["not_run", "passed", "failed"])
  status!: ArtifactRecord["benchmarkStatus"];
}

class DetectorProfileDto {
  @IsString()
  @Matches(safeDetectorId)
  detectorId!: string;

  @IsNumber({ maxDecimalPlaces: 3 })
  @Min(0)
  @Max(1)
  sensitivity!: number;

  @IsInt()
  @Min(0)
  @Max(60_000)
  cooldownMs!: number;

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(5)
  @ArrayUnique()
  @IsIn(wakeStates, { each: true })
  allowedStates!: string[];
}

class WakeProfileDto {
  @IsString()
  @Matches(safeArtifactId)
  artifactId!: string;

  @IsString()
  @Length(1, 80)
  name!: string;

  @IsLocale()
  locale!: string;

  @IsIn(["development", "canary", "stable"])
  channel!: string;

  @IsString()
  @Length(1, 80)
  activationPhrase!: string;

  @ValidateNested()
  @Type(() => DetectorProfileDto)
  activation!: DetectorProfileDto;

  @ValidateNested()
  @Type(() => DetectorProfileDto)
  interrupt!: DetectorProfileDto;
}

class ResourceRolloutDto {
  @IsUUID("4")
  wakeProfileId!: string;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(2_147_483_647)
  version?: number;

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(100)
  @ArrayUnique()
  @IsUUID("4", { each: true })
  deviceIds!: string[];
}

@Controller("api/v1")
export class ResourceCatalogController {
  constructor(private readonly resources: ResourceCatalogService) {}

  @Get("artifacts")
  async artifacts(@CurrentPrincipal() principal: Principal): Promise<ArtifactRecord[]> {
    return this.resources.listArtifacts(principal.tenantId);
  }

  @Roles(TenantRole.ADMIN)
  @Post("artifacts/register")
  async register(
    @Body() input: RegisterArtifactDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<ArtifactRecord> {
    return this.resources.registerArtifact(
      input.artifactId,
      input.license,
      input.benchmarkStatus,
      { principal, requestId: request.id },
    );
  }

  @Roles(TenantRole.ADMIN)
  @Post("artifacts/:id/publish")
  async publishArtifact(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<ArtifactRecord> {
    return this.resources.publishArtifact(id, { principal, requestId: request.id });
  }

  @Roles(TenantRole.ADMIN)
  @Patch("artifacts/:id/benchmark")
  async benchmark(
    @Param("id") id: string,
    @Body() input: ArtifactBenchmarkDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<ArtifactRecord> {
    return this.resources.updateBenchmark(id, input.status, {
      principal,
      requestId: request.id,
    });
  }

  @Get("wake-profiles")
  async wakeProfiles(@CurrentPrincipal() principal: Principal): Promise<WakeProfileRecord[]> {
    return this.resources.listWakeProfiles(principal.tenantId);
  }

  @Roles(TenantRole.OPERATOR)
  @Post("wake-profiles")
  async createWakeProfile(
    @Body() input: WakeProfileDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<WakeProfileRecord> {
    return this.resources.createWakeProfile(input, { principal, requestId: request.id });
  }

  @Roles(TenantRole.OPERATOR)
  @Patch("wake-profiles/:id")
  async updateWakeProfile(
    @Param("id") id: string,
    @Body() input: WakeProfileDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<WakeProfileRecord> {
    return this.resources.updateWakeProfile(id, input, {
      principal,
      requestId: request.id,
    });
  }

  @Roles(TenantRole.OPERATOR)
  @Post("wake-profiles/:id/publish")
  async publishWakeProfile(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<WakeProfileRecord> {
    return this.resources.publishWakeProfile(id, { principal, requestId: request.id });
  }

  @Get("resource-rollouts")
  async rollouts(@CurrentPrincipal() principal: Principal): Promise<ResourceRolloutRecord[]> {
    return this.resources.listRollouts(principal.tenantId);
  }

  @Roles(TenantRole.OPERATOR)
  @Post("resource-rollouts")
  async rollout(
    @Body() input: ResourceRolloutDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<ResourceRolloutRecord[]> {
    return this.resources.rollout(
      input.wakeProfileId,
      input.version,
      input.deviceIds,
      { principal, requestId: request.id },
    );
  }
}
