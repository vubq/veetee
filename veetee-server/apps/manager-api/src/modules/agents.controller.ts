import { Body, Controller, Get, Param, Patch, Post, Req } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { IsIn, IsLocale, IsObject, IsOptional, IsString, Length } from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import { ControlPlaneStore, type AgentRecord } from "../store/control-plane.store.js";

class CreateAgentDto {
  @IsString()
  @Length(1, 80)
  name!: string;

  @IsLocale()
  defaultLocale!: string;

  @IsIn(["auto", "manual", "realtime"])
  interactionMode!: "auto" | "manual" | "realtime";

  @IsString()
  @Length(1, 20_000)
  persona!: string;

  @IsOptional()
  @IsObject()
  draftConfig?: Record<string, unknown>;
}

class UpdateAgentDto {
  @IsOptional()
  @IsString()
  @Length(1, 80)
  name?: string;

  @IsOptional()
  @IsLocale()
  defaultLocale?: string;

  @IsOptional()
  @IsIn(["auto", "manual", "realtime"])
  interactionMode?: "auto" | "manual" | "realtime";

  @IsOptional()
  @IsString()
  @Length(1, 20_000)
  persona?: string;

  @IsOptional()
  @IsObject()
  draftConfig?: Record<string, unknown>;
}

@Controller("api/v1/agents")
export class AgentsController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get()
  async list(@CurrentPrincipal() principal: Principal): Promise<AgentRecord[]> {
    return this.store.listAgents(principal.tenantId);
  }

  @Roles(TenantRole.OPERATOR)
  @Post()
  async create(
    @Body() input: CreateAgentDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<AgentRecord> {
    return this.store.createAgent(input, { principal, requestId: request.id });
  }

  @Roles(TenantRole.OPERATOR)
  @Patch(":id")
  async update(
    @Param("id") id: string,
    @Body() input: UpdateAgentDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<AgentRecord> {
    return this.store.updateAgent(id, input, { principal, requestId: request.id });
  }

  @Roles(TenantRole.OPERATOR)
  @Post(":id/publish")
  async publish(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<AgentRecord> {
    return this.store.publishAgent(id, { principal, requestId: request.id });
  }
}
