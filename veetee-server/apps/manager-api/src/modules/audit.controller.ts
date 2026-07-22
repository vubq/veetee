import { Controller, Get, Query } from "@nestjs/common";
import { Type } from "class-transformer";
import { IsInt, IsOptional, IsString, Max, MaxLength, Min } from "class-validator";

import type { Principal } from "../auth/auth.types.js";
import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { ControlPlaneStore, type AuditEventRecord } from "../store/control-plane.store.js";

export class AuditQueryDto {
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(200)
  limit = 100;

  @IsOptional()
  @IsString()
  @MaxLength(100)
  action?: string;

  @IsOptional()
  @IsString()
  @MaxLength(64)
  targetType?: string;
}

@Controller("api/v1/audit-events")
export class AuditController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get()
  list(
    @Query() query: AuditQueryDto,
    @CurrentPrincipal() principal: Principal,
  ): Promise<AuditEventRecord[]> {
    return this.store.listAuditEvents(principal.tenantId, query);
  }
}
