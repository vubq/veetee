import { createHash, randomBytes, randomInt } from "node:crypto";

import { BadRequestException, HttpException, HttpStatus, Injectable } from "@nestjs/common";

import { RedisService } from "../database/redis.service.js";

export interface PairingTicket {
  hardwareId: string;
  challenge: string;
  expiresAt: number;
}

interface PairingAllocation extends PairingTicket {
  code: string;
}

@Injectable()
export class PairingService {
  constructor(private readonly redis: RedisService) {}

  async create(hardwareId: string, ttlSeconds = 600): Promise<{
    code: string;
    challenge: string;
    expiresAt: string;
  }> {
    const active = await this.activeAllocation(hardwareId);
    if (active) return this.publicAllocation(active);

    for (let attempt = 0; attempt < 20; attempt += 1) {
      const code = randomInt(0, 1_000_000).toString().padStart(6, "0");
      const challenge = randomBytes(24).toString("base64url");
      const expiresAt = Date.now() + ttlSeconds * 1_000;
      const ticketStored = await this.redis.client.set(
        this.ticketKey(code),
        JSON.stringify({ hardwareId, challenge, expiresAt } satisfies PairingTicket),
        "EX",
        ttlSeconds,
        "NX",
      );
      if (ticketStored !== "OK") continue;

      const allocation = { code, hardwareId, challenge, expiresAt } satisfies PairingAllocation;
      const hardwareStored = await this.redis.client.set(
        this.hardwareKey(hardwareId),
        JSON.stringify(allocation),
        "EX",
        ttlSeconds,
        "NX",
      );
      if (hardwareStored === "OK") return this.publicAllocation(allocation);

      await this.redis.client.del(this.ticketKey(code));
      const winner = await this.activeAllocation(hardwareId);
      if (winner) {
        return this.publicAllocation(winner);
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

  private hardwareKey(hardwareId: string): string {
    const digest = createHash("sha256").update(hardwareId).digest("hex");
    return `pairing:hardware:${digest}`;
  }

  private async activeAllocation(hardwareId: string): Promise<PairingAllocation | null> {
    const key = this.hardwareKey(hardwareId);
    const encoded = await this.redis.client.get(key);
    if (!encoded) return null;
    try {
      const allocation = JSON.parse(encoded) as PairingAllocation;
      const ticket = await this.redis.client.get(this.ticketKey(allocation.code));
      if (
        allocation.hardwareId === hardwareId &&
        allocation.expiresAt > Date.now() &&
        ticket !== null
      ) {
        return allocation;
      }
    } catch {
      // Invalid or stale internal state is removed and replaced below.
    }
    await this.redis.client.del(key);
    return null;
  }

  private publicAllocation(allocation: PairingAllocation): {
    code: string;
    challenge: string;
    expiresAt: string;
  } {
    return {
      code: allocation.code,
      challenge: allocation.challenge,
      expiresAt: new Date(allocation.expiresAt).toISOString(),
    };
  }
}
