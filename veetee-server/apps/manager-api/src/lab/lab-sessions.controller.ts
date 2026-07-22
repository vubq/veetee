import { Body, Controller, Post, Req, UseGuards } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { IsIn, IsOptional, IsString, IsUUID, Length } from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Public } from "../auth/public.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import { ServiceTokenGuard } from "../auth/service-token.guard.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  LabSessionService,
  type LabInputMode,
  type LabMcpMode,
} from "./lab-session.service.js";

class CreateLabSessionDto {
  @IsUUID("4")
  agentId!: string;

  @IsIn(["text", "audio_replay", "live_mic"])
  inputMode!: LabInputMode;

  @IsIn(["simulated", "selected_device", "disabled"])
  mcpMode!: LabMcpMode;

  @IsOptional()
  @IsUUID("4")
  deviceId?: string;
}

class ConsumeLabSessionDto {
  @IsString()
  @Length(64, 2_048)
  token!: string;
}

@Controller("api/v1/lab/sessions")
export class LabSessionsController {
  constructor(private readonly sessions: LabSessionService) {}

  @Roles(TenantRole.OPERATOR)
  @Post()
  async create(
    @Body() input: CreateLabSessionDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<Record<string, unknown>> {
    return this.sessions.create(input, principal, request.id);
  }
}

@Public()
@UseGuards(ServiceTokenGuard)
@Controller("internal/v1/lab/sessions")
export class InternalLabSessionsController {
  constructor(private readonly sessions: LabSessionService) {}

  @Post("consume")
  async consume(@Body() input: ConsumeLabSessionDto): Promise<Record<string, unknown>> {
    return { ...(await this.sessions.consume(input.token)) };
  }
}
