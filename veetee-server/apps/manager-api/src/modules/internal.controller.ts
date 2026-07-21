import { Body, Controller, Get, Param, ParseIntPipe, Post, Query, UseGuards } from "@nestjs/common";
import { ArrayMaxSize, ArrayMinSize, IsArray, IsString, IsUUID, Length } from "class-validator";

import { Public } from "../auth/public.decorator.js";
import { ServiceTokenGuard } from "../auth/service-token.guard.js";
import { ControlPlaneStore } from "../store/control-plane.store.js";

class AuthenticateDeviceDto {
  @IsString()
  @Length(4, 128)
  hardwareId!: string;

  @IsString()
  @Length(32, 256)
  token!: string;
}

class ResolveProvidersDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(24)
  @IsUUID("4", { each: true })
  providerIds!: string[];
}

@Public()
@UseGuards(ServiceTokenGuard)
@Controller("internal/v1")
export class InternalController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get("agent-configs/:agentId")
  async agentConfig(
    @Param("agentId") agentId: string,
    @Query("version", new ParseIntPipe({ optional: true })) version?: number,
  ): Promise<Record<string, unknown>> {
    return this.store.getAgentConfig(agentId, version);
  }

  @Post("devices/authenticate")
  async authenticate(@Body() input: AuthenticateDeviceDto): Promise<Record<string, unknown>> {
    return this.store.authenticateDeviceByHardware(input.hardwareId, input.token);
  }

  @Post("providers/resolve")
  async resolveProviders(@Body() input: ResolveProvidersDto) {
    return this.store.resolveProviderRuntime(input.providerIds);
  }
}
