import { BadRequestException, Body, Controller, Param, Post, Req } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { IsOptional, IsString, Length, Matches } from "class-validator";

import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Public } from "../auth/public.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import { ControlPlaneStore, type DeviceRecord } from "../store/control-plane.store.js";

class CreatePairingCodeDto {
  @IsString()
  @Length(4, 128)
  hardwareId!: string;
}

class ClaimPairingDto {
  @IsString()
  @Length(1, 80)
  name!: string;

  @IsOptional()
  @IsString()
  agentId?: string;
}

class ActivateDeviceDto {
  @IsString()
  @Length(4, 128)
  hardwareId!: string;

  @IsString()
  @Length(16, 128)
  challenge!: string;
}

@Controller()
export class PairingController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Public()
  @Post(["device-edge/pairing-codes", "veetee/ota/pairing"])
  async createCode(@Body() input: CreatePairingCodeDto): Promise<{
    code: string;
    challenge: string;
    expiresAt: string;
  }> {
    return this.store.createPairingCode(input.hardwareId);
  }

  @Roles(TenantRole.OPERATOR)
  @Post("api/v1/devices/activation/:code/bind")
  async claim(
    @Param("code") code: string,
    @Body() input: ClaimPairingDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<DeviceRecord> {
    if (!/^\d{6}$/.test(code)) throw new BadRequestException("Pairing code must contain six digits");
    return this.store.claimPairing(code, input.name, { principal, requestId: request.id }, input.agentId);
  }

  @Public()
  @Post("veetee/ota/activate")
  async activate(@Body() input: ActivateDeviceDto): Promise<Record<string, unknown>> {
    return this.store.activateDevice(input.hardwareId, input.challenge);
  }
}
