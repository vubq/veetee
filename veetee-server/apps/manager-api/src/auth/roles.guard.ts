import { type CanActivate, type ExecutionContext, ForbiddenException, Injectable } from "@nestjs/common";
import { Reflector } from "@nestjs/core";
import { TenantRole } from "@prisma/client";

import type { RequestWithPrincipal } from "./auth.types.js";
import { REQUIRED_ROLES } from "./roles.decorator.js";

const roleRank: Record<TenantRole, number> = {
  [TenantRole.VIEWER]: 0,
  [TenantRole.OPERATOR]: 1,
  [TenantRole.ADMIN]: 2,
  [TenantRole.OWNER]: 3,
};

@Injectable()
export class RolesGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    const required = this.reflector.getAllAndOverride<TenantRole[]>(REQUIRED_ROLES, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (!required?.length) return true;
    const request = context.switchToHttp().getRequest<RequestWithPrincipal>();
    if (!request.principal) return false;
    const minimum = Math.min(...required.map((role) => roleRank[role]));
    if (roleRank[request.principal.role] < minimum) {
      throw new ForbiddenException("Insufficient tenant role");
    }
    return true;
  }
}
