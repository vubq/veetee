import { createHash } from "node:crypto";

import { HttpException, HttpStatus, Injectable } from "@nestjs/common";

import { RedisService } from "../database/redis.service.js";

const MAX_FAILURES = 8;
const WINDOW_SECONDS = 10 * 60;
const RECORD_FAILURE_SCRIPT = `
local count = redis.call("INCR", KEYS[1])
if count == 1 then redis.call("EXPIRE", KEYS[1], ARGV[1]) end
return count
`;

@Injectable()
export class LoginRateLimitService {
  constructor(private readonly redis: RedisService) {}

  async assertAllowed(email: string): Promise<void> {
    const failures = Number((await this.redis.client.get(this.key(email))) ?? "0");
    if (failures >= MAX_FAILURES) this.reject();
  }

  async recordFailure(email: string): Promise<void> {
    const failures = Number(
      await this.redis.client.eval(
        RECORD_FAILURE_SCRIPT,
        1,
        this.key(email),
        WINDOW_SECONDS,
      ),
    );
    if (failures >= MAX_FAILURES) this.reject();
  }

  async reset(email: string): Promise<void> {
    await this.redis.client.del(this.key(email));
  }

  private key(email: string): string {
    const identity = createHash("sha256").update(email.trim().toLowerCase()).digest("hex");
    return `veetee:auth-login-failures:${identity}`;
  }

  private reject(): never {
    throw new HttpException(
      "Too many failed login attempts; retry later",
      HttpStatus.TOO_MANY_REQUESTS,
    );
  }
}
