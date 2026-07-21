import { Body, Controller, Post } from "@nestjs/common";
import { IsOptional, IsString, Length, Matches } from "class-validator";

import { ControlPlaneStore, type DeviceRecord } from "../store/control-plane.store.js";

class CreatePairingCodeDto {
  @IsString()
  @Length(4, 128)
  hardwareId!: string;
}

class ClaimPairingDto {
  @Matches(/^\d{6}$/)
  code!: string;

  @IsString()
  @Length(1, 80)
  name!: string;

  @IsOptional()
  @IsString()
  agentId?: string;
}

@Controller()
export class PairingController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Post("device-edge/pairing-codes")
  createCode(@Body() input: CreatePairingCodeDto): { code: string; expiresAt: string } {
    return this.store.createPairingCode(input.hardwareId);
  }

  @Post("api/pairing/claim")
  claim(@Body() input: ClaimPairingDto): DeviceRecord {
    return this.store.claimPairing(input.code, input.name, input.agentId);
  }
}
