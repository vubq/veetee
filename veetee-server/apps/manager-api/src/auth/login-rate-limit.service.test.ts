import { describe, expect, test, vi } from "vitest";

import type { RedisService } from "../database/redis.service.js";
import { LoginRateLimitService } from "./login-rate-limit.service.js";

function service(initialFailures = 0) {
  let failures = initialFailures;
  const client = {
    get: vi.fn(async () => (failures ? String(failures) : null)),
    eval: vi.fn(async () => {
      failures += 1;
      return failures;
    }),
    del: vi.fn(async () => {
      failures = 0;
      return 1;
    }),
  };
  return {
    client,
    limiter: new LoginRateLimitService({ client } as unknown as RedisService),
  };
}

describe("LoginRateLimitService", () => {
  test("allows failures below the bounded window and resets after success", async () => {
    const { client, limiter } = service(2);

    await limiter.assertAllowed("Owner@Veetee.Local");
    await limiter.recordFailure("owner@veetee.local");
    await limiter.reset("owner@veetee.local");

    expect(client.eval).toHaveBeenCalledOnce();
    expect(client.del).toHaveBeenCalledOnce();
    await expect(limiter.assertAllowed("owner@veetee.local")).resolves.toBeUndefined();
  });

  test("rejects the eighth failed attempt for the same normalized identity", async () => {
    const { limiter } = service(7);

    await expect(limiter.recordFailure("owner@veetee.local")).rejects.toMatchObject({
      status: 429,
    });
    await expect(limiter.assertAllowed("OWNER@VEETEE.LOCAL")).rejects.toMatchObject({
      status: 429,
    });
  });
});
