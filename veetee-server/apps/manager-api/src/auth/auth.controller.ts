import { Body, Controller, Get, Post, Req, Res, UnauthorizedException } from "@nestjs/common";
import { IsEmail, IsOptional, IsString, Length, MinLength } from "class-validator";
import type { FastifyReply } from "fastify";

import { AuthService, type TokenPair } from "./auth.service.js";
import type { RequestWithPrincipal } from "./auth.types.js";
import { CurrentPrincipal } from "./current-principal.decorator.js";
import type { Principal } from "./auth.types.js";
import { LoginRateLimitService } from "./login-rate-limit.service.js";
import { Public } from "./public.decorator.js";

const REFRESH_COOKIE = "veetee_refresh";

class LoginDto {
  @IsEmail()
  email!: string;

  @IsString()
  @MinLength(8)
  @Length(8, 256)
  password!: string;

  @IsOptional()
  @IsString()
  @Length(1, 80)
  tenantSlug?: string;
}

@Controller("api/v1/auth")
export class AuthController {
  constructor(
    private readonly auth: AuthService,
    private readonly loginRateLimit: LoginRateLimitService,
  ) {}

  @Public()
  @Post("login")
  async login(
    @Body() input: LoginDto,
    @Req() request: RequestWithPrincipal,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<Record<string, unknown>> {
    await this.loginRateLimit.assertAllowed(input.email);
    let pair: TokenPair;
    try {
      pair = await this.auth.login(input.email, input.password, input.tenantSlug);
    } catch (error) {
      if (error instanceof UnauthorizedException) {
        await this.loginRateLimit.recordFailure(input.email);
      }
      throw error;
    }
    await this.loginRateLimit.reset(input.email);
    this.setRefreshCookie(request, reply, pair);
    return this.publicTokenResponse(pair);
  }

  @Public()
  @Post("refresh")
  async refresh(
    @Req() request: RequestWithPrincipal,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<Record<string, unknown>> {
    const refreshToken = request.cookies?.[REFRESH_COOKIE];
    if (!refreshToken) throw new UnauthorizedException("Refresh cookie is missing");
    const pair = await this.auth.refresh(refreshToken);
    this.setRefreshCookie(request, reply, pair);
    return this.publicTokenResponse(pair);
  }

  @Public()
  @Post("logout")
  async logout(
    @Req() request: RequestWithPrincipal,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<{ status: string }> {
    await this.auth.logout(request.cookies?.[REFRESH_COOKIE]);
    reply.clearCookie(REFRESH_COOKIE, { path: "/api/v1/auth" });
    return { status: "ok" };
  }

  @Get("me")
  me(@CurrentPrincipal() principal: Principal): Principal {
    return principal;
  }

  private setRefreshCookie(
    request: RequestWithPrincipal,
    reply: FastifyReply,
    pair: TokenPair,
  ): void {
    const forwardedProto = request.headers["x-forwarded-proto"];
    const protocol = (Array.isArray(forwardedProto) ? forwardedProto[0] : forwardedProto)
      ?.split(",")[0]
      ?.trim();
    reply.setCookie(REFRESH_COOKIE, pair.refreshToken, {
      httpOnly: true,
      sameSite: "strict",
      secure: protocol === "https" || process.env.NODE_ENV === "production",
      path: "/api/v1/auth",
      expires: pair.refreshExpiresAt,
    });
  }

  private publicTokenResponse(pair: TokenPair): Record<string, unknown> {
    return {
      accessToken: pair.accessToken,
      tokenType: "Bearer",
      expiresIn: pair.accessExpiresIn,
      principal: pair.principal,
    };
  }
}
