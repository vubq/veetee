import {
  Body,
  Controller,
  Get,
  Param,
  Patch,
  Post,
  Put,
  Req,
  UseGuards,
} from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { Type } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsBoolean,
  IsIn,
  IsInt,
  IsISO8601,
  IsNumber,
  IsOptional,
  IsString,
  IsUrl,
  IsUUID,
  Length,
  Matches,
  Max,
  MaxLength,
  Min,
  ValidateIf,
  ValidateNested,
} from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Public } from "../auth/public.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import { ServiceTokenGuard } from "../auth/service-token.guard.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  RemoteMcpService,
  type RemoteMcpAssignmentInput,
  type RemoteMcpAuditInput,
  type RemoteMcpEndpointInput,
  type RemoteMcpSafetyClass,
  type RemoteMcpToolPolicy,
} from "../mcp/remote-mcp.service.js";

export class RemoteMcpToolPolicyDto implements RemoteMcpToolPolicy {
  @IsString()
  @Matches(/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/)
  name!: string;

  @IsIn(["read_only", "reversible", "disruptive", "destructive"])
  safetyClass!: RemoteMcpSafetyClass;

  @IsBoolean()
  requiresConfirmation!: boolean;
}

export class CreateRemoteMcpEndpointDto implements RemoteMcpEndpointInput {
  @IsString()
  @Length(1, 80)
  name!: string;

  @IsUrl({ require_tld: false, protocols: ["http", "https"], require_protocol: true })
  @Length(8, 2_048)
  url!: string;

  @IsIn(["streamable_http", "sse"])
  transport!: "streamable_http" | "sse";

  @IsIn(["none", "bearer", "header"])
  authType!: "none" | "bearer" | "header";

  @IsOptional()
  @IsString()
  @Matches(/^[A-Za-z][A-Za-z0-9-]{0,63}$/)
  authHeaderName?: string;

  @IsOptional()
  @IsString()
  @Length(1, 4_096)
  secret?: string;

  @IsNumber({ maxDecimalPlaces: 2 })
  @Min(5)
  @Max(30)
  timeoutSeconds!: number;

  @IsInt()
  @Min(1_024)
  @Max(65_536)
  resultMaxBytes!: number;

  @IsIn(["public_only", "private_allowlist"])
  networkPolicy!: "public_only" | "private_allowlist";

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(1)
  @IsString({ each: true })
  @MaxLength(253, { each: true })
  allowedHosts!: string[];

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(128)
  @ValidateNested({ each: true })
  @Type(() => RemoteMcpToolPolicyDto)
  tools!: RemoteMcpToolPolicyDto[];
}

class UpdateRemoteMcpEndpointDto {
  @IsOptional()
  @IsBoolean()
  enabled?: boolean;

  @IsOptional()
  @IsIn(["keep", "rotate", "clear"])
  secretAction?: "keep" | "rotate" | "clear";

  @IsOptional()
  @ValidateIf((input: UpdateRemoteMcpEndpointDto) => input.secretAction === "rotate")
  @IsString()
  @Length(1, 4_096)
  secret?: string;
}

export class RemoteMcpAssignmentDto implements RemoteMcpAssignmentInput {
  @IsUUID("4")
  endpointId!: string;

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(128)
  @IsString({ each: true })
  @Matches(/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/, { each: true })
  toolNames!: string[];

  @IsNumber({ maxDecimalPlaces: 2 })
  @Min(5)
  @Max(30)
  timeoutSeconds!: number;
}

class ReplaceRemoteMcpAssignmentsDto {
  @IsArray()
  @ArrayMaxSize(32)
  @ValidateNested({ each: true })
  @Type(() => RemoteMcpAssignmentDto)
  assignments!: RemoteMcpAssignmentDto[];
}

class ResolveRemoteMcpDto {
  @IsUUID("4")
  agentId!: string;

  @IsUUID("4")
  deviceId!: string;

  @IsInt()
  @Min(1)
  @Max(2_147_483_647)
  configVersion!: number;
}

export class RemoteMcpAuditDto implements RemoteMcpAuditInput {
  @IsUUID("4")
  eventId!: string;

  @IsUUID("4")
  endpointId!: string;

  @IsUUID("4")
  agentId!: string;

  @IsUUID("4")
  deviceId!: string;

  @IsInt()
  @Min(1)
  @Max(2_147_483_647)
  configVersion!: number;

  @IsString()
  @Matches(/^[A-Za-z0-9][A-Za-z0-9:_-]{7,159}$/)
  sessionId!: string;

  @IsString()
  @Matches(/^[A-Za-z0-9][A-Za-z0-9:_-]{7,159}$/)
  turnId!: string;

  @IsString()
  @Matches(/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/)
  toolName!: string;

  @IsString()
  @Matches(/^[a-f0-9]{64}$/)
  argumentsHash!: string;

  @IsIn(["succeeded", "failed", "cancelled", "stale", "completed_after_abort"])
  status!: RemoteMcpAuditInput["status"];

  @IsInt()
  @Min(0)
  @Max(3_600_000)
  durationMs!: number;

  @IsIn(["model", "user", "system"])
  actor!: RemoteMcpAuditInput["actor"];

  @IsISO8601({ strict: true })
  occurredAt!: string;
}

@Controller("api/v1/mcp/endpoints")
export class RemoteMcpController {
  constructor(private readonly remoteMcp: RemoteMcpService) {}

  @Get()
  list(@CurrentPrincipal() principal: Principal) {
    return this.remoteMcp.listEndpoints(principal.tenantId);
  }

  @Get(":id")
  endpoint(@Param("id") id: string, @CurrentPrincipal() principal: Principal) {
    return this.remoteMcp.endpoint(principal.tenantId, id);
  }

  @Roles(TenantRole.ADMIN)
  @Post()
  create(
    @Body() input: CreateRemoteMcpEndpointDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.remoteMcp.createEndpoint(input, { principal, requestId: request.id });
  }

  @Roles(TenantRole.ADMIN)
  @Patch(":id")
  update(
    @Param("id") id: string,
    @Body() input: UpdateRemoteMcpEndpointDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.remoteMcp.updateEndpoint(id, input, { principal, requestId: request.id });
  }

  @Roles(TenantRole.OPERATOR)
  @Post(":id/test")
  test(
    @Param("id") id: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.remoteMcp.testEndpoint(id, { principal, requestId: request.id });
  }
}

@Controller("api/v1/agents/:agentId/mcp-endpoints")
export class AgentRemoteMcpController {
  constructor(private readonly remoteMcp: RemoteMcpService) {}

  @Get()
  list(
    @Param("agentId") agentId: string,
    @CurrentPrincipal() principal: Principal,
  ) {
    return this.remoteMcp.listAssignments(principal.tenantId, agentId);
  }

  @Roles(TenantRole.ADMIN)
  @Put()
  replace(
    @Param("agentId") agentId: string,
    @Body() input: ReplaceRemoteMcpAssignmentsDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.remoteMcp.replaceAssignments(agentId, input.assignments, {
      principal,
      requestId: request.id,
    });
  }
}

@Public()
@UseGuards(ServiceTokenGuard)
@Controller("internal/v1/remote-mcp")
export class InternalRemoteMcpController {
  constructor(private readonly remoteMcp: RemoteMcpService) {}

  @Post("resolve")
  resolve(@Body() input: ResolveRemoteMcpDto) {
    return this.remoteMcp.resolve(input.agentId, input.deviceId, input.configVersion);
  }

  @Post("audit")
  audit(@Body() input: RemoteMcpAuditDto) {
    return this.remoteMcp.recordInvocation(input);
  }
}
