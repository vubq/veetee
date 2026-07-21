import { describe, expect, it } from "vitest";

import type { RedisService } from "../database/redis.service.js";
import { PairingService } from "../pairing/pairing.service.js";

class FakeRedisClient {
  private readonly values = new Map<string, string>();
  private readonly attempts = new Map<string, number>();

  async set(key: string, value: string): Promise<"OK" | null> {
    if (this.values.has(key)) return null;
    this.values.set(key, value);
    return "OK";
  }

  async get(key: string): Promise<string | null> {
    return this.values.get(key) ?? null;
  }

  async del(key: string): Promise<number> {
    return this.values.delete(key) ? 1 : 0;
  }

  async eval(
    _: string,
    __: number,
    attemptKey: string,
    ticketKey: string,
    ___: number,
    maxAttempts: number,
  ): Promise<string[]> {
    const attempts = (this.attempts.get(attemptKey) ?? 0) + 1;
    this.attempts.set(attemptKey, attempts);
    if (attempts > maxAttempts) return ["rate_limited"];
    const ticket = this.values.get(ticketKey);
    if (!ticket) return ["missing"];
    this.values.delete(ticketKey);
    return ["ok", ticket];
  }
}

describe("PairingService", () => {
  it("creates a six digit single-use pairing code", async () => {
    const redis = { client: new FakeRedisClient() } as unknown as RedisService;
    const pairing = new PairingService(redis);
    const ticket = await pairing.create("esp32-test");
    expect(ticket.code).toMatch(/^\d{6}$/);
    expect((await pairing.consume(ticket.code, "owner")).hardwareId).toBe("esp32-test");
    await expect(pairing.consume(ticket.code, "owner")).rejects.toThrow();
  });

  it("reuses the active ticket for bootstrap retries", async () => {
    const redis = { client: new FakeRedisClient() } as unknown as RedisService;
    const pairing = new PairingService(redis);
    const first = await pairing.create("esp32-retry");
    const retry = await pairing.create("esp32-retry");
    expect(retry).toEqual(first);
  });
});
