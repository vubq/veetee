import type { Principal } from "../api/schemas";

export type TenantRole = Principal["role"];

const roleRank: Record<TenantRole, number> = {
  VIEWER: 0,
  OPERATOR: 1,
  ADMIN: 2,
  OWNER: 3,
};

export function hasMinimumRole(role: TenantRole | undefined, minimum: TenantRole): boolean {
  return role !== undefined && roleRank[role] >= roleRank[minimum];
}

export function canUseMemory(role: TenantRole | undefined): boolean {
  return hasMinimumRole(role, "OPERATOR");
}

export function canTestRemoteMcp(role: TenantRole | undefined): boolean {
  return hasMinimumRole(role, "OPERATOR");
}

export function canManageRemoteMcp(role: TenantRole | undefined): boolean {
  return hasMinimumRole(role, "ADMIN");
}
