import { Body, Controller, Delete, Get, Param, Patch, Post, Req } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { IsIn, IsLocale, IsObject, IsOptional, IsString, Length } from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  PERSONALITY_ACCENTS,
  type AgentPromptCatalog,
  type PersonalityPreset,
} from "../config/agent-prompt.policy.js";
import {
  ControlPlaneStore,
  type AgentRecord,
  type PersonalityPresetInput,
} from "../store/control-plane.store.js";

class CreateAgentDto {
  @IsString()
  @Length(1, 80)
  name!: string;

  @IsLocale()
  defaultLocale!: string;

  @IsIn(["auto", "manual", "realtime"])
  interactionMode!: "auto" | "manual" | "realtime";

  @IsOptional()
  @IsString()
  @Length(0, 20_000)
  persona?: string;

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
  @Length(0, 20_000)
  persona?: string;

  @IsOptional()
  @IsObject()
  draftConfig?: Record<string, unknown>;
}

class CreatePersonalityPresetDto implements PersonalityPresetInput {
  @IsString()
  @Length(1, 80)
  label!: string;

  @IsString()
  @Length(1, 240)
  summary!: string;

  @IsIn(PERSONALITY_ACCENTS)
  accent!: string;

  @IsString()
  @Length(1, 4_000)
  instructions!: string;
}

@Controller("api/v1/agents")
export class AgentsController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get("prompt-catalog")
  promptCatalog(@CurrentPrincipal() principal: Principal): Promise<AgentPromptCatalog> {
    return this.store.getAgentPromptCatalog(principal.tenantId);
  }

  @Roles(TenantRole.OPERATOR)
  @Post("personality-presets")
  createPersonalityPreset(
    @Body() input: CreatePersonalityPresetDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<PersonalityPreset> {
    return this.store.createPersonalityPreset(input, {
      principal,
      requestId: request.id,
    });
  }

  @Roles(TenantRole.OPERATOR)
  @Delete("personality-presets/:id")
  deletePersonalityPreset(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<PersonalityPreset> {
    return this.store.deletePersonalityPreset(id, {
      principal,
      requestId: request.id,
    });
  }

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
