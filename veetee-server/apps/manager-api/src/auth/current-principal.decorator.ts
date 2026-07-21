import { createParamDecorator, type ExecutionContext } from "@nestjs/common";

import type { Principal, RequestWithPrincipal } from "./auth.types.js";

export const CurrentPrincipal = createParamDecorator(
  (_: unknown, context: ExecutionContext): Principal => {
    const request = context.switchToHttp().getRequest<RequestWithPrincipal>();
    if (!request.principal) throw new Error("Authenticated principal is missing");
    return request.principal;
  },
);
