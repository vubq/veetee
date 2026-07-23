import { Module } from "@nestjs/common";
import { APP_GUARD } from "@nestjs/core";

import { AuditService } from "./audit/audit.service.js";
import { ArtifactFilesService } from "./artifacts/artifact-files.service.js";
import { ArtifactsController } from "./artifacts/artifacts.controller.js";
import { ResourceCatalogController } from "./artifacts/resource-catalog.controller.js";
import { ResourceCatalogService } from "./artifacts/resource-catalog.service.js";
import { ResourceManifestService } from "./artifacts/resource-manifest.service.js";
import { UiPackUploadService } from "./artifacts/ui-pack-upload.service.js";
import { UiPacksController } from "./artifacts/ui-packs.controller.js";
import { FirmwareRolloutController } from "./ota/firmware-rollout.controller.js";
import { AuthController } from "./auth/auth.controller.js";
import { AuthGuard } from "./auth/auth.guard.js";
import { LoginRateLimitService } from "./auth/login-rate-limit.service.js";
import { AuthService } from "./auth/auth.service.js";
import { DeviceAuthGuard } from "./auth/device-auth.guard.js";
import { RolesGuard } from "./auth/roles.guard.js";
import { ServiceTokenGuard } from "./auth/service-token.guard.js";
import { BootstrapService } from "./database/bootstrap.service.js";
import { PrismaService } from "./database/prisma.service.js";
import { RedisService } from "./database/redis.service.js";
import { DeviceDiagnosticsService } from "./diagnostics/device-diagnostics.service.js";
import { LabSessionService } from "./lab/lab-session.service.js";
import {
  InternalLabSessionsController,
  LabSessionsController,
} from "./lab/lab-sessions.controller.js";
import { AgentsController } from "./modules/agents.controller.js";
import { AuditController } from "./modules/audit.controller.js";
import { ConversationEventsController } from "./modules/conversation-events.controller.js";
import { DeviceDiagnosticsController } from "./modules/device-diagnostics.controller.js";
import { DevicesController } from "./modules/devices.controller.js";
import { HealthController } from "./modules/health.controller.js";
import { InternalController } from "./modules/internal.controller.js";
import { McpController } from "./modules/mcp.controller.js";
import { OtaController } from "./modules/ota.controller.js";
import { OperationsController } from "./modules/operations.controller.js";
import { PairingController } from "./modules/pairing.controller.js";
import { ProvidersController } from "./modules/providers.controller.js";
import { PairingService } from "./pairing/pairing.service.js";
import { VoiceMcpService } from "./mcp/voice-mcp.service.js";
import { SecretCryptoService } from "./security/secret-crypto.service.js";
import { ControlPlaneStore } from "./store/control-plane.store.js";
import { FirmwareRolloutService } from "./ota/firmware-rollout.service.js";

@Module({
  controllers: [
    AuthController,
    ArtifactsController,
    ResourceCatalogController,
    UiPacksController,
    FirmwareRolloutController,
    HealthController,
    OtaController,
    PairingController,
    DevicesController,
    AgentsController,
    AuditController,
    ConversationEventsController,
    DeviceDiagnosticsController,
    ProvidersController,
    LabSessionsController,
    McpController,
    InternalController,
    InternalLabSessionsController,
    OperationsController,
  ],
  providers: [
    PrismaService,
    RedisService,
    BootstrapService,
    AuditService,
    ArtifactFilesService,
    ResourceManifestService,
    ResourceCatalogService,
    UiPackUploadService,
    AuthService,
    LoginRateLimitService,
    PairingService,
    VoiceMcpService,
    DeviceDiagnosticsService,
    SecretCryptoService,
    LabSessionService,
    ControlPlaneStore,
    FirmwareRolloutService,
    DeviceAuthGuard,
    ServiceTokenGuard,
    { provide: APP_GUARD, useClass: AuthGuard },
    { provide: APP_GUARD, useClass: RolesGuard },
  ],
})
export class AppModule {}
