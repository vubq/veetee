import { Controller, Get } from "@nestjs/common";

import type { Principal } from "../auth/auth.types.js";
import { CurrentPrincipal } from "../auth/current-principal.decorator.js";

export interface OperationsProfile {
  deployment: {
    mode: "single_node";
    domainRequired: false;
    managerApiUrl: string;
    voiceWebsocketUrl: string;
  };
  privacy: {
    rawAudioStored: false;
    transcriptStored: false;
    conversationEventRetentionDays: number;
  };
  security: {
    deviceScopedTokens: true;
    signedArtifacts: true;
    publicTlsRequired: false;
  };
  firmware: {
    configuredVersion: string;
    releaseConfigured: boolean;
    otaRoute: "/veetee/ota/";
  };
}

@Controller("api/v1/operations")
export class OperationsController {
  @Get("profile")
  profile(@CurrentPrincipal() _principal: Principal): OperationsProfile {
    const managerApiUrl = process.env.VEETEE_MANAGER_PUBLIC_URL ?? "http://127.0.0.1:8001";
    const voiceWebsocketUrl = process.env.VEETEE_VOICE_WS_URL ?? "ws://127.0.0.1:8000/veetee/v1/";
    const retention = Number(process.env.VEETEE_CONVERSATION_EVENT_RETENTION_DAYS ?? 7);
    const conversationEventRetentionDays = Number.isFinite(retention)
      ? Math.min(30, Math.max(1, Math.trunc(retention)))
      : 7;
    const configuredVersion = process.env.VEETEE_FIRMWARE_VERSION ?? "unknown";
    return {
      deployment: {
        mode: "single_node",
        domainRequired: false,
        managerApiUrl,
        voiceWebsocketUrl,
      },
      privacy: {
        rawAudioStored: false,
        transcriptStored: false,
        conversationEventRetentionDays,
      },
      security: {
        deviceScopedTokens: true,
        signedArtifacts: true,
        publicTlsRequired: false,
      },
      firmware: {
        configuredVersion,
        releaseConfigured: Boolean(process.env.VEETEE_FIRMWARE_URL),
        otaRoute: "/veetee/ota/",
      },
    };
  }
}
