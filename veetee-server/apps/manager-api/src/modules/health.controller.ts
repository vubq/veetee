import { Controller, Get } from "@nestjs/common";

import { Public } from "../auth/public.decorator.js";
import { ControlPlaneStore } from "../store/control-plane.store.js";

@Public()
@Controller("health")
export class HealthController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get("live")
  live(): { status: string; service: string } {
    return { status: "ok", service: "manager-api" };
  }

  @Get("ready")
  async ready(): Promise<Record<string, unknown>> {
    return { status: "ready", components: await this.store.health() };
  }
}
