import { Body, Controller, Get, Param, Patch, Post, Req } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsBoolean,
  IsIn,
  IsInt,
  IsOptional,
  IsString,
  IsUrl,
  Length,
  Max,
  Min,
  ValidateIf,
} from "class-validator";

import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import { ControlPlaneStore, type ProviderRecord } from "../store/control-plane.store.js";

class CreateProviderDto {
  @IsIn(["vad", "asr", "llm", "tts", "realtime", "memory"])
  kind!: ProviderRecord["kind"];

  @IsString()
  @Length(1, 120)
  adapter!: string;

  @IsString()
  @Length(1, 200)
  model!: string;

  @IsOptional()
  @IsUrl({ require_tld: false })
  baseUrl?: string;

  @IsOptional()
  @IsString()
  @Length(1, 4_096)
  secret?: string;

  @IsBoolean()
  enabled!: boolean;

  @IsOptional()
  @IsInt()
  @Min(0)
  @Max(1_000)
  priority?: number;

  @IsOptional()
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(16)
  @IsString({ each: true })
  locales?: string[];
}

class UpdateProviderDto {
  @IsOptional()
  @IsString()
  @Length(1, 120)
  adapter?: string;

  @IsOptional()
  @IsString()
  @Length(1, 200)
  model?: string;

  @IsOptional()
  @ValidateIf((_, value) => value !== null)
  @IsUrl({ require_tld: false })
  baseUrl?: string | null;

  @IsOptional()
  @IsBoolean()
  enabled?: boolean;

  @IsOptional()
  @IsInt()
  @Min(0)
  @Max(1_000)
  priority?: number;

  @IsOptional()
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(16)
  @IsString({ each: true })
  locales?: string[];

  @IsOptional()
  @IsIn(["keep", "rotate", "clear"])
  secretAction?: "keep" | "rotate" | "clear";

  @IsOptional()
  @IsString()
  @Length(1, 4_096)
  secret?: string;
}

@Controller("api/v1/providers")
export class ProvidersController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get()
  async list(@CurrentPrincipal() principal: Principal): Promise<ProviderRecord[]> {
    return this.store.listProviders(principal.tenantId);
  }

  @Roles(TenantRole.ADMIN)
  @Post()
  async create(
    @Body() input: CreateProviderDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<ProviderRecord> {
    return this.store.createProvider(input, { principal, requestId: request.id });
  }

  @Roles(TenantRole.ADMIN)
  @Patch(":id")
  async update(
    @Param("id") id: string,
    @Body() input: UpdateProviderDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<ProviderRecord> {
    return this.store.updateProvider(id, input, { principal, requestId: request.id });
  }

  @Roles(TenantRole.OPERATOR)
  @Post(":id/test")
  async test(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<ProviderRecord> {
    return this.store.testProvider(id, { principal, requestId: request.id });
  }
}
