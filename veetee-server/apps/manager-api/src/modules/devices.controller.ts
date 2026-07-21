import { Body, Controller, Get, Param, Put, Req, UseGuards } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { IsInt, IsObject, IsOptional, IsString, Min } from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { DeviceAuthGuard } from "../auth/device-auth.guard.js";
import { Public } from "../auth/public.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import { ControlPlaneStore, type DeviceRecord } from "../store/control-plane.store.js";

class DesiredStateDto {
  @IsObject()
  state!: Record<string, unknown>;
}

class ReportedStateDto {
  @IsInt()
  @Min(0)
  version!: number;

  @IsObject()
  state!: Record<string, unknown>;

  @IsOptional()
  @IsString()
  bootId?: string;
}

@Controller()
export class DevicesController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get("api/v1/devices")
  async list(@CurrentPrincipal() principal: Principal): Promise<DeviceRecord[]> {
    return this.store.listDevices(principal.tenantId);
  }

  @Public()
  @UseGuards(DeviceAuthGuard)
  @Get("veetee/config/v1/devices/:id")
  async desired(@Param("id") id: string): Promise<DeviceRecord["desiredState"]> {
    const device = await this.store.deviceForAuthenticatedDevice(id);
    return device.desiredState;
  }

  @Roles(TenantRole.OPERATOR)
  @Put("api/v1/devices/:id/desired-state")
  async setDesired(
    @Param("id") id: string,
    @Body() input: DesiredStateDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<DeviceRecord> {
    return this.store.setDesiredState(id, input.state, { principal, requestId: request.id });
  }

  @Public()
  @UseGuards(DeviceAuthGuard)
  @Put("veetee/devices/:id/reported-state")
  async report(@Param("id") id: string, @Body() input: ReportedStateDto): Promise<DeviceRecord> {
    return this.store.updateReportedState(id, input.version, input.state, input.bootId);
  }
}
