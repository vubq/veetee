import { Body, Controller, Get, Param, Post, Req } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { IsBoolean, IsIn, IsOptional, IsString, IsUrl, Length } from "class-validator";

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
