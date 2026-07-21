import { Module } from "@nestjs/common";

import { AgentsController } from "./modules/agents.controller.js";
import { DevicesController } from "./modules/devices.controller.js";
import { HealthController } from "./modules/health.controller.js";
import { McpController } from "./modules/mcp.controller.js";
import { PairingController } from "./modules/pairing.controller.js";
import { ProvidersController } from "./modules/providers.controller.js";
import { ControlPlaneStore } from "./store/control-plane.store.js";

@Module({
  controllers: [
    HealthController,
    PairingController,
    DevicesController,
    AgentsController,
    ProvidersController,
    McpController,
  ],
  providers: [ControlPlaneStore],
})
export class AppModule {}
