import { createHash, randomBytes, randomInt } from "node:crypto";

import { BadRequestException, HttpException, HttpStatus, Injectable } from "@nestjs/common";

import { RedisService } from "../database/redis.service.js";

export interface PairingTicket {
  hardwareId: string;
  challenge: string;
  expiresAt: number;
}

@Injectable()
export class PairingService {
  constructor(private readonly redis: RedisService) {}

  async create(hardwareId: string, ttlSeconds = 600): Promise<{
    code: string;
    challenge: string;
    expiresAt: string;
  }> {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const code = randomInt(0, 1_000_000).toString().padStart(6, "0");
      const challenge = randomBytes(24).toString("base64url");
      const expiresAt = Date.now() + ttlSeconds * 1_000;
      const stored = await this.redis.client.set(
        this.ticketKey(code),
        JSON.stringify({ hardwareId, challenge, expiresAt } satisfies PairingTicket),
        "EX",
        ttlSeconds,
        "NX",
      );
      if (stored === "OK") {
        return { code, challenge, expiresAt: new Date(expiresAt).toISOString() };
      }
    }
    throw new BadRequestException("Unable to allocate a pairing code");
  }

  async consume(code: string, actorKey: string): Promise<PairingTicket> {
    const result = (await this.redis.client.eval(
      `
local attempts = redis.call("INCR", KEYS[1])
if attempts == 1 then redis.call("EXPIRE", KEYS[1], ARGV[1]) end
if attempts > tonumber(ARGV[2]) then return {"rate_limited"} end
local ticket = redis.call("GET", KEYS[2])
if not ticket then return {"missing"} end
redis.call("DEL", KEYS[2])
return {"ok", ticket}
`,
      2,
      `pairing:attempts:${actorKey}`,
      this.ticketKey(code),
      60,
      10,
    )) as string[];
    if (result[0] === "rate_limited") {
      throw new HttpException("Pairing claim rate limit exceeded", HttpStatus.TOO_MANY_REQUESTS);
    }
    if (result[0] !== "ok" || !result[1]) {
      throw new BadRequestException("Pairing code is invalid or expired");
    }
    return JSON.parse(result[1]) as PairingTicket;
  }

  private ticketKey(code: string): string {
    const digest = createHash("sha256").update(code).digest("hex");
    return `pairing:code:${digest}`;
  }
}
