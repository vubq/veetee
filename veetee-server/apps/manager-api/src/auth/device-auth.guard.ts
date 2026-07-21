import { type CanActivate, type ExecutionContext, Injectable, UnauthorizedException } from "@nestjs/common";

import { ControlPlaneStore } from "../store/control-plane.store.js";
import type { RequestWithPrincipal } from "./auth.types.js";

@Injectable()
export class DeviceAuthGuard implements CanActivate {
  constructor(private readonly store: ControlPlaneStore) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<RequestWithPrincipal>();
    const deviceId = request.params?.id;
    const authorization = request.headers.authorization;
    const value = Array.isArray(authorization) ? authorization[0] : authorization;
    if (!deviceId || !value?.startsWith("Bearer ")) {
      throw new UnauthorizedException("Device authorization is missing");
    }
    await this.store.authenticateDevice(deviceId, value.slice(7));
    return true;
  }
}
