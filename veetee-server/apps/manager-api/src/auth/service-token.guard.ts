import { timingSafeEqual } from "node:crypto";

import { type CanActivate, type ExecutionContext, Injectable, UnauthorizedException } from "@nestjs/common";

import type { RequestWithPrincipal } from "./auth.types.js";

@Injectable()
export class ServiceTokenGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const expected = process.env.VEETEE_INTERNAL_SERVICE_TOKEN;
    if (!expected || expected.length < 24) {
      throw new Error("VEETEE_INTERNAL_SERVICE_TOKEN must contain at least 24 characters");
    }
    const request = context.switchToHttp().getRequest<RequestWithPrincipal>();
    const authorization = request.headers.authorization;
    const value = Array.isArray(authorization) ? authorization[0] : authorization;
    if (!value?.startsWith("Bearer ") || !this.matches(value.slice(7), expected)) {
      throw new UnauthorizedException("Service token is invalid");
    }
    return true;
  }

  private matches(actual: string, expected: string): boolean {
    const actualBytes = Buffer.from(actual);
    const expectedBytes = Buffer.from(expected);
    return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes);
  }
}
