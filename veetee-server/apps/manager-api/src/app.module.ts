import { Module } from "@nestjs/common";
import { APP_GUARD } from "@nestjs/core";

import { AuditService } from "./audit/audit.service.js";
import { ArtifactFilesService } from "./artifacts/artifact-files.service.js";
import { ArtifactsController } from "./artifacts/artifacts.controller.js";
import { ResourceCatalogController } from "./artifacts/resource-catalog.controller.js";
import { ResourceCatalogService } from "./artifacts/resource-catalog.service.js";
import { ResourceManifestService } from "./artifacts/resource-manifest.service.js";
import { AuthController } from "./auth/auth.controller.js";
import { AuthGuard } from "./auth/auth.guard.js";
import { AuthService } from "./auth/auth.service.js";
import { DeviceAuthGuard } from "./auth/device-auth.guard.js";
import { RolesGuard } from "./auth/roles.guard.js";
import { ServiceTokenGuard } from "./auth/service-token.guard.js";
import { BootstrapService } from "./database/bootstrap.service.js";
import { PrismaService } from "./database/prisma.service.js";
import { RedisService } from "./database/redis.service.js";
import { AgentsController } from "./modules/agents.controller.js";
import { ConversationEventsController } from "./modules/conversation-events.controller.js";
import { DevicesController } from "./modules/devices.controller.js";
import { HealthController } from "./modules/health.controller.js";
import { InternalController } from "./modules/internal.controller.js";
import { McpController } from "./modules/mcp.controller.js";
import { OtaController } from "./modules/ota.controller.js";
import { PairingController } from "./modules/pairing.controller.js";
import { ProvidersController } from "./modules/providers.controller.js";
import { PairingService } from "./pairing/pairing.service.js";
import { VoiceMcpService } from "./mcp/voice-mcp.service.js";
import { SecretCryptoService } from "./security/secret-crypto.service.js";
import { ControlPlaneStore } from "./store/control-plane.store.js";

@Module({
  controllers: [
    AuthController,
    ArtifactsController,
    ResourceCatalogController,
    HealthController,
    OtaController,
    PairingController,
    DevicesController,
    AgentsController,
    ConversationEventsController,
    ProvidersController,
    McpController,
    InternalController,
  ],
  providers: [
    PrismaService,
    RedisService,
    BootstrapService,
    AuditService,
    ArtifactFilesService,
    ResourceManifestService,
    ResourceCatalogService,
    AuthService,
    PairingService,
    VoiceMcpService,
    SecretCryptoService,
    ControlPlaneStore,
    DeviceAuthGuard,
    ServiceTokenGuard,
    { provide: APP_GUARD, useClass: AuthGuard },
    { provide: APP_GUARD, useClass: RolesGuard },
  ],
})
export class AppModule {}
