import { Body, Controller, Get, Post, Query, UseGuards } from "@nestjs/common";
import { Type } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsISO8601,
  IsInt,
  IsObject,
  IsOptional,
  IsString,
  IsUUID,
  Matches,
  Max,
  MaxLength,
  Min,
  ValidateNested,
} from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Public } from "../auth/public.decorator.js";
import { ServiceTokenGuard } from "../auth/service-token.guard.js";
import type { Principal } from "../auth/auth.types.js";
import {
  ControlPlaneStore,
  type ConversationEventRecord,
} from "../store/control-plane.store.js";

export class ConversationEventDto {
  @IsUUID("4")
  eventId!: string;

  @IsString()
  @Matches(/^[A-Za-z0-9][A-Za-z0-9:_-]{7,127}$/)
  sessionId!: string;

  @IsOptional()
  @IsString()
  @MaxLength(160)
  @Matches(/^[A-Za-z0-9][A-Za-z0-9:_-]+$/)
  turnId?: string;

  @IsInt()
  @Min(0)
  @Max(2_147_483_647)
  generation!: number;

  @IsString()
  @Matches(/^[a-z][a-z0-9_.-]{0,63}$/)
  eventType!: string;

  @IsObject()
  payload!: Record<string, unknown>;

  @IsISO8601({ strict: true })
  occurredAt!: string;
}

class ConversationEventBatchDto {
  @IsUUID("4")
  deviceId!: string;

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(64)
  @ValidateNested({ each: true })
  @Type(() => ConversationEventDto)
  events!: ConversationEventDto[];
}

class ConversationEventQueryDto {
  @IsOptional()
  @IsUUID("4")
  deviceId?: string;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(200)
  limit = 100;
}

@Controller()
export class ConversationEventsController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Public()
  @UseGuards(ServiceTokenGuard)
  @Post("internal/v1/conversation-events/batch")
  async ingest(
    @Body() input: ConversationEventBatchDto,
  ): Promise<{ accepted: number }> {
    return this.store.ingestConversationEvents(input.deviceId, input.events);
  }

  @Get("api/v1/conversation-events")
  async list(
    @Query() query: ConversationEventQueryDto,
    @CurrentPrincipal() principal: Principal,
  ): Promise<ConversationEventRecord[]> {
    return this.store.listConversationEvents(
      principal.tenantId,
      query.deviceId,
      query.limit,
    );
  }
}
