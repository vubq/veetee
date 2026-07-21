import { Body, Controller, Get, Param, Put } from "@nestjs/common";
import { IsObject } from "class-validator";

import { ControlPlaneStore, type DeviceRecord } from "../store/control-plane.store.js";

class StateDto {
  @IsObject()
  state!: Record<string, unknown>;
}

@Controller()
export class DevicesController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get("api/devices")
  list(): DeviceRecord[] {
    return this.store.listDevices();
  }

  @Get("device-edge/devices/:id/desired-state")
  desired(@Param("id") id: string): Record<string, unknown> {
    return this.store.device(id).desiredState;
  }

  @Put("api/devices/:id/desired-state")
  setDesired(@Param("id") id: string, @Body() input: StateDto): DeviceRecord {
    return this.store.setDesiredState(id, input.state);
  }

  @Put("device-edge/devices/:id/reported-state")
  report(@Param("id") id: string, @Body() input: StateDto): DeviceRecord {
    return this.store.updateReportedState(id, input.state);
  }
}
