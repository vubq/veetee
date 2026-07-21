import { createHash, randomBytes } from "node:crypto";

import { verify } from "@node-rs/argon2";
import { Injectable, UnauthorizedException } from "@nestjs/common";
import { jwtVerify, SignJWT } from "jose";

import { PrismaService } from "../database/prisma.service.js";
import type { Principal } from "./auth.types.js";

const ACCESS_TOKEN_SECONDS = 15 * 60;
const REFRESH_TOKEN_SECONDS = 30 * 24 * 60 * 60;

export interface TokenPair {
  accessToken: string;
  accessExpiresIn: number;
  refreshToken: string;
  refreshExpiresAt: Date;
  principal: Principal;
}

@Injectable()
export class AuthService {
  constructor(private readonly prisma: PrismaService) {}

  async login(email: string, password: string, tenantSlug?: string): Promise<TokenPair> {
    const user = await this.prisma.user.findUnique({
      where: { email: email.toLowerCase() },
      include: {
        memberships: {
          include: { tenant: true },
          orderBy: { createdAt: "asc" },
        },
      },
    });
    if (!user || !user.active || !(await verify(user.passwordHash, password))) {
      throw new UnauthorizedException("Invalid credentials");
    }
    const membership = tenantSlug
      ? user.memberships.find((candidate) => candidate.tenant.slug === tenantSlug)
      : user.memberships[0];
    if (!membership) throw new UnauthorizedException("Tenant membership not found");
    const principal: Principal = {
      userId: user.id,
      tenantId: membership.tenantId,
      tenantSlug: membership.tenant.slug,
      role: membership.role,
      email: user.email,
      displayName: user.displayName,
    };
    return this.issueTokenPair(principal);
  }

  async refresh(refreshToken: string): Promise<TokenPair> {
    const tokenHash = this.hashToken(refreshToken);
    const session = await this.prisma.refreshSession.findUnique({
      where: { tokenHash },
      include: {
        tenant: true,
        user: { include: { memberships: true } },
      },
    });
    if (
      !session ||
      session.revokedAt ||
      session.expiresAt <= new Date() ||
      !session.user.active
    ) {
      throw new UnauthorizedException("Refresh token is invalid or expired");
    }
    const membership = session.user.memberships.find(
      (candidate) => candidate.tenantId === session.tenantId,
    );
    if (!membership) throw new UnauthorizedException("Tenant membership not found");
    const consumed = await this.prisma.refreshSession.updateMany({
      where: { id: session.id, revokedAt: null },
      data: { revokedAt: new Date() },
    });
    if (consumed.count !== 1) {
      throw new UnauthorizedException("Refresh token is invalid or expired");
    }
    return this.issueTokenPair({
      userId: session.user.id,
      tenantId: session.tenantId,
      tenantSlug: session.tenant.slug,
      role: membership.role,
      email: session.user.email,
      displayName: session.user.displayName,
    });
  }

  async logout(refreshToken: string | undefined): Promise<void> {
    if (!refreshToken) return;
    await this.prisma.refreshSession.updateMany({
      where: { tokenHash: this.hashToken(refreshToken), revokedAt: null },
      data: { revokedAt: new Date() },
    });
  }

  async verifyAccessToken(token: string): Promise<Principal> {
    try {
      const result = await jwtVerify(token, this.authKey(), {
        issuer: "veetee-manager",
        audience: "veetee-manager-web",
      });
      const payload = result.payload;
      if (
        payload.token_type !== "access" ||
        typeof payload.sub !== "string" ||
        typeof payload.tenant_id !== "string" ||
        typeof payload.tenant_slug !== "string" ||
        typeof payload.role !== "string" ||
        typeof payload.email !== "string" ||
        typeof payload.display_name !== "string"
      ) {
        throw new Error("Invalid claims");
      }
      return {
        userId: payload.sub,
        tenantId: payload.tenant_id,
        tenantSlug: payload.tenant_slug,
        role: payload.role as Principal["role"],
        email: payload.email,
        displayName: payload.display_name,
      };
    } catch {
      throw new UnauthorizedException("Access token is invalid or expired");
    }
  }

  private async issueTokenPair(principal: Principal): Promise<TokenPair> {
    const accessToken = await new SignJWT({
      token_type: "access",
      tenant_id: principal.tenantId,
      tenant_slug: principal.tenantSlug,
      role: principal.role,
      email: principal.email,
      display_name: principal.displayName,
    })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject(principal.userId)
      .setIssuer("veetee-manager")
      .setAudience("veetee-manager-web")
      .setIssuedAt()
      .setExpirationTime(`${ACCESS_TOKEN_SECONDS}s`)
      .sign(this.authKey());
    const refreshToken = randomBytes(48).toString("base64url");
    const refreshExpiresAt = new Date(Date.now() + REFRESH_TOKEN_SECONDS * 1_000);
    await this.prisma.refreshSession.create({
      data: {
        userId: principal.userId,
        tenantId: principal.tenantId,
        tokenHash: this.hashToken(refreshToken),
        expiresAt: refreshExpiresAt,
      },
    });
    return {
      accessToken,
      accessExpiresIn: ACCESS_TOKEN_SECONDS,
      refreshToken,
      refreshExpiresAt,
      principal,
    };
  }

  private authKey(): Uint8Array {
    const secret = process.env.VEETEE_AUTH_SECRET;
    if (!secret || secret.length < 32) {
      throw new Error("VEETEE_AUTH_SECRET must contain at least 32 characters");
    }
    return new TextEncoder().encode(secret);
  }

  private hashToken(token: string): string {
    return createHash("sha256").update(token).digest("hex");
  }
}
