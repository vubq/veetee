import type { TenantRole } from "@prisma/client";

export interface Principal {
  userId: string;
  tenantId: string;
  tenantSlug: string;
  role: TenantRole;
  email: string;
  displayName: string;
}

export interface RequestWithPrincipal {
  id: string;
  headers: Record<string, string | string[] | undefined>;
  params?: Record<string, string | undefined>;
  principal?: Principal;
  cookies?: Record<string, string | undefined>;
}
