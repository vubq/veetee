import {
  Body,
  Controller,
  Delete,
  Get,
  Header,
  Param,
  Patch,
  Post,
  Query,
  Req,
  UseGuards,
} from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { Type } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsIn,
  IsInt,
  IsISO8601,
  IsNumber,
  IsOptional,
  IsString,
  IsUUID,
  Length,
  Matches,
  Max,
  MaxLength,
  Min,
  ValidateNested,
} from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Public } from "../auth/public.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import { ServiceTokenGuard } from "../auth/service-token.guard.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  MemoryService,
  type MemoryFactInput,
  type MemoryMessageInput,
} from "../memory/memory.service.js";

const SESSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9:_-]{7,159}$/;
const IDEMPOTENCY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9:._-]{7,199}$/;

class InternalMemoryScopeDto {
  @IsUUID("4")
  agentId!: string;

  @IsUUID("4")
  deviceId!: string;

  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(2_147_483_647)
  configVersion!: number;
}

export class MemoryMessageDto implements MemoryMessageInput {
  @IsString()
  @Matches(IDEMPOTENCY_PATTERN)
  idempotencyKey!: string;

  @IsString()
  @Matches(SESSION_PATTERN)
  sessionId!: string;

  @IsString()
  @Matches(SESSION_PATTERN)
  turnId!: string;

  @IsIn(["user", "assistant"])
  role!: "user" | "assistant";

  @IsString()
  @Length(1, 4_000)
  content!: string;

  @IsISO8601({ strict: true })
  occurredAt!: string;
}

class MemoryMessageBatchDto extends InternalMemoryScopeDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(64)
  @ValidateNested({ each: true })
  @Type(() => MemoryMessageDto)
  messages!: MemoryMessageDto[];
}

export class MemoryFactDto implements MemoryFactInput {
  @IsString()
  @Matches(IDEMPOTENCY_PATTERN)
  idempotencyKey!: string;

  @IsString()
  @Matches(/^[a-z][a-z0-9_.-]{0,63}$/)
  category!: string;

  @IsString()
  @Length(1, 120)
  key!: string;

  @IsString()
  @Length(1, 2_000)
  value!: string;

  @IsNumber({ maxDecimalPlaces: 4 })
  @Min(0)
  @Max(1)
  confidence!: number;

  @IsString()
  @Matches(SESSION_PATTERN)
  sourceSessionId!: string;

  @IsString()
  @Matches(SESSION_PATTERN)
  sourceTurnId!: string;

  @IsInt()
  @Min(1)
  @Max(365)
  expiresInDays!: number;
}

class MemoryFactBatchDto extends InternalMemoryScopeDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(32)
  @ValidateNested({ each: true })
  @Type(() => MemoryFactDto)
  facts!: MemoryFactDto[];
}

class MemoryPageQueryDto {
  @IsUUID("4")
  deviceId!: string;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(100)
  limit = 50;

  @IsOptional()
  @IsString()
  @MaxLength(512)
  cursor?: string;
}

class MemoryPurgeQueryDto {
  @IsUUID("4")
  deviceId!: string;
}

class MemoryExportDto {
  @IsUUID("4")
  deviceId!: string;
}

class UpdateMemoryFactDto {
  @IsOptional()
  @IsString()
  @Length(1, 2_000)
  value?: string;

  @IsOptional()
  @IsNumber({ maxDecimalPlaces: 4 })
  @Min(0)
  @Max(1)
  confidence?: number;

  @IsOptional()
  @IsISO8601({ strict: true })
  expiresAt?: string;
}

@Public()
@UseGuards(ServiceTokenGuard)
@Controller("internal/v1/memory")
export class InternalMemoryController {
  constructor(private readonly memory: MemoryService) {}

  @Get("context")
  context(@Query() query: InternalMemoryScopeDto) {
    return this.memory.getContext(query.agentId, query.deviceId, query.configVersion);
  }

  @Post("messages/batch")
  appendMessages(@Body() input: MemoryMessageBatchDto) {
    return this.memory.appendMessages(
      input.agentId,
      input.deviceId,
      input.configVersion,
      input.messages,
    );
  }

  @Post("facts/batch")
  upsertFacts(@Body() input: MemoryFactBatchDto) {
    return this.memory.upsertFacts(
      input.agentId,
      input.deviceId,
      input.configVersion,
      input.facts,
    );
  }
}

@Roles(TenantRole.OPERATOR)
@Controller("api/v1/agents/:agentId/memory")
export class MemoryController {
  constructor(private readonly memory: MemoryService) {}

  @Get("messages")
  listMessages(
    @Param("agentId") agentId: string,
    @Query() query: MemoryPageQueryDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.memory.listMessages(
      principal.tenantId,
      agentId,
      query.deviceId,
      query.limit,
      query.cursor,
      { principal, requestId: request.id },
    );
  }

  @Post("exports")
  @Header("Cache-Control", "no-store")
  exportMemory(
    @Param("agentId") agentId: string,
    @Body() input: MemoryExportDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.memory.exportMemory(agentId, input.deviceId, {
      principal,
      requestId: request.id,
    });
  }

  @Delete("messages")
  purgeMessages(
    @Param("agentId") agentId: string,
    @Query() query: MemoryPurgeQueryDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.memory.purgeMessages(agentId, query.deviceId, {
      principal,
      requestId: request.id,
    });
  }

  @Delete("messages/:messageId")
  deleteMessage(
    @Param("agentId") agentId: string,
    @Param("messageId") messageId: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.memory.deleteMessage(agentId, messageId, {
      principal,
      requestId: request.id,
    });
  }

  @Get("facts")
  listFacts(
    @Param("agentId") agentId: string,
    @Query() query: MemoryPageQueryDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.memory.listFacts(
      principal.tenantId,
      agentId,
      query.deviceId,
      query.limit,
      query.cursor,
      { principal, requestId: request.id },
    );
  }

  @Patch("facts/:factId")
  updateFact(
    @Param("agentId") agentId: string,
    @Param("factId") factId: string,
    @Body() input: UpdateMemoryFactDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.memory.updateFact(agentId, factId, input, {
      principal,
      requestId: request.id,
    });
  }

  @Delete("facts/:factId")
  deleteFact(
    @Param("agentId") agentId: string,
    @Param("factId") factId: string,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ) {
    return this.memory.deleteFact(agentId, factId, {
      principal,
      requestId: request.id,
    });
  }
}
