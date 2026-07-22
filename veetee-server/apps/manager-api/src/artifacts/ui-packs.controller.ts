import { Body, Controller, Get, Headers, Param, Post, Req } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import {
  ArrayMaxSize,
  ArrayMinSize,
  ArrayUnique,
  IsArray,
  IsUUID,
} from "class-validator";

import { CurrentPrincipal } from "../auth/current-principal.decorator.js";
import { Roles } from "../auth/roles.decorator.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import {
  ResourceCatalogService,
  type ArtifactRecord,
  type UiPackRolloutRecord,
} from "./resource-catalog.service.js";
import { UiPackUploadService } from "./ui-pack-upload.service.js";

class UiPackRolloutDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(100)
  @ArrayUnique()
  @IsUUID("4", { each: true })
  deviceIds!: string[];
}

@Controller("api/v1/ui-packs")
export class UiPacksController {
  constructor(
    private readonly uploads: UiPackUploadService,
    private readonly resources: ResourceCatalogService,
  ) {}

  @Roles(TenantRole.ADMIN)
  @Post("uploads")
  async upload(
    @Body() body: unknown,
    @Headers("x-veetee-file-name") fileName: string | undefined,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<ArtifactRecord> {
    return this.uploads.stage(body, fileName, { principal, requestId: request.id });
  }

  @Get("rollouts")
  async rollouts(@CurrentPrincipal() principal: Principal): Promise<UiPackRolloutRecord[]> {
    return this.resources.listUiPackRollouts(principal.tenantId);
  }

  @Roles(TenantRole.OPERATOR)
  @Post(":id/rollout")
  async rollout(
    @Param("id") id: string,
    @Body() input: UiPackRolloutDto,
    @CurrentPrincipal() principal: Principal,
    @Req() request: RequestWithPrincipal,
  ): Promise<UiPackRolloutRecord[]> {
    return this.resources.rolloutUiPack(id, input.deviceIds, {
      principal,
      requestId: request.id,
    });
  }
}
