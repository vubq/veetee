import { Controller, Get } from "@nestjs/common";

@Controller("health")
export class HealthController {
  @Get("live")
  live(): { status: string; service: string } {
    return { status: "ok", service: "manager-api" };
  }
}
