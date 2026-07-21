import { SetMetadata } from "@nestjs/common";
import type { TenantRole } from "@prisma/client";

export const REQUIRED_ROLES = "veetee:required-roles";
export const Roles = (...roles: TenantRole[]): MethodDecorator & ClassDecorator =>
  SetMetadata(REQUIRED_ROLES, roles);
