import {
  Body,
  ConflictException,
  Controller,
  Get,
  Logger,
  Param,
  Post,
  Req,
} from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import {
  IsBoolean,
  IsNumber,
  IsObject,
  IsOptional,
  Matches,
  Max,
  Min,
} from "class-validator";

import { AuditService } from "../audit/audit.service.js";
import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import { VoiceMcpService, type McpToolRecord } from "../mcp/voice-mcp.service.js";
import { ControlPlaneStore } from "../store/control-plane.store.js";

class DeviceToolPathDto {
  @Matches(/^self\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$/)
  name!: string;
}

class DeviceToolCallDto {
  @IsObject()
  arguments!: Record<string, unknown>;

  @IsBoolean()
  confirmed!: boolean;

  @IsOptional()
  @IsNumber({ maxDecimalPlaces: 2 })
  @Min(0.5)
  @Max(30)
  timeoutSeconds = 10;
}

@Controller()
export class McpController {
  private readonly logger = new Logger(McpController.name);

  constructor(
    private readonly store: ControlPlaneStore,
    private readonly voiceMcp: VoiceMcpService,
    private readonly audit: AuditService,
  ) {}

  @Get("api/v1/mcp/tools")
  listBaseline(): McpToolRecord[] {
    return [
      {
        name: "self.get_device_status",
        description: "Read the current device status.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        audience: "regular",
        safetyClass: "read_only",
        requiresConfirmation: false,
      },
      {
        name: "self.audio_speaker.set_volume",
        description: "Set speaker volume from 0 to 100.",
        inputSchema: {
          type: "object",
          properties: { volume: { type: "integer", minimum: 0, maximum: 100 } },
          required: ["volume"],
          additionalProperties: false,
        },
        audience: "regular",
        safetyClass: "reversible",
        requiresConfirmation: false,
      },
    ];
  }

  @Roles(TenantRole.OPERATOR)
  @Get("api/v1/devices/:id/mcp/tools")
  async listDeviceTools(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
  ): Promise<McpToolRecord[]> {
    await this.store.device(principal.tenantId, id);
    return this.voiceMcp.listTools(id);
  }

  @Roles(TenantRole.OPERATOR)
  @Post("api/v1/devices/:id/mcp/tools/:name/call")
  async callDeviceTool(
    @Param("id") id: string,
    @Param() path: DeviceToolPathDto,
    @Body() input: DeviceToolCallDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<Record<string, unknown>> {
    await this.store.device(principal.tenantId, id);
    const catalog = await this.voiceMcp.listTools(id);
    const tool = catalog.find((item) => item.name === path.name);
    if (!tool) throw new ConflictException("Device MCP catalog changed; refresh and retry");
    if (tool.requiresConfirmation && !input.confirmed) {
      throw new ConflictException("Device MCP tool requires explicit confirmation");
    }
    await this.audit.record({
      tenantId: principal.tenantId,
      actorUserId: principal.userId,
      action: "device.mcp.call.requested",
      targetType: "device",
      targetId: id,
      requestId: request.id,
      before: { tool: path.name, arguments: input.arguments },
      details: {
        tool: path.name,
        confirmed: input.confirmed,
        safetyClass: tool.safetyClass,
        audience: tool.audience,
      },
    });
    try {
      const result = await this.voiceMcp.callTool(
        id,
        path.name,
        input.arguments,
        input.confirmed,
        input.timeoutSeconds,
      );
      try {
        await this.audit.record({
          tenantId: principal.tenantId,
          actorUserId: principal.userId,
          action: "device.mcp.call.succeeded",
          targetType: "device",
          targetId: id,
          requestId: request.id,
          after: result,
          details: { tool: path.name },
        });
      } catch (auditError) {
        this.logger.error(
          "MCP call succeeded but outcome audit failed",
          auditError instanceof Error ? auditError.stack : undefined,
        );
      }
      return result;
    } catch (error) {
      try {
        await this.audit.record({
          tenantId: principal.tenantId,
          actorUserId: principal.userId,
          action: "device.mcp.call.failed",
          targetType: "device",
          targetId: id,
          requestId: request.id,
          details: {
            tool: path.name,
            error: error instanceof Error ? error.constructor.name : "UnknownError",
          },
        });
      } catch (auditError) {
        this.logger.error(
          "MCP call failed and outcome audit also failed",
          auditError instanceof Error ? auditError.stack : undefined,
        );
      }
      throw error;
    }
  }
}
