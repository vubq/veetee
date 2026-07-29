import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { TenantRole } from "@prisma/client";
import { describe, expect, it, vi } from "vitest";

import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import type { MemoryService } from "../memory/memory.service.js";
import {
  InternalMemoryController,
  MemoryController,
  MemoryFactDto,
  MemoryMessageDto,
} from "./memory.controller.js";

const principal: Principal = {
  userId: "user-1",
  tenantId: "tenant-1",
  tenantSlug: "tenant",
  role: TenantRole.OPERATOR,
  email: "operator@example.test",
  displayName: "Operator",
};

const message = {
  idempotencyKey: "session_12345678:turn_12345678:user",
  sessionId: "session_12345678",
  turnId: "turn_12345678",
  role: "user",
  content: "Tôi thích trà sen.",
  occurredAt: "2026-07-29T04:00:00.000Z",
};

describe("memory API contracts", () => {
  it("accepts bounded text-only messages and facts", async () => {
    await expect(validate(plainToInstance(MemoryMessageDto, message))).resolves.toEqual([]);
    await expect(validate(plainToInstance(MemoryFactDto, {
      idempotencyKey: "session_12345678:turn_12345678:fact:drink",
      category: "preference",
      key: "favorite_drink",
      value: "Trà sen",
      confidence: 0.92,
      sourceSessionId: "session_12345678",
      sourceTurnId: "turn_12345678",
      expiresInDays: 90,
    }))).resolves.toEqual([]);
  });

  it("rejects unbounded content and invalid fact confidence", async () => {
    expect(await validate(plainToInstance(MemoryMessageDto, {
      ...message,
      content: "x".repeat(4_001),
    }))).not.toEqual([]);
    expect(await validate(plainToInstance(MemoryFactDto, {
      idempotencyKey: "session_12345678:turn_12345678:fact:drink",
      category: "preference",
      key: "favorite_drink",
      value: "Trà sen",
      confidence: 1.5,
      sourceSessionId: "session_12345678",
      sourceTurnId: "turn_12345678",
      expiresInDays: 90,
    }))).not.toEqual([]);
  });

  it("derives public tenant scope from the authenticated principal", async () => {
    const memory = {
      listMessages: vi.fn().mockResolvedValue({ items: [] }),
    };
    const controller = new MemoryController(memory as unknown as MemoryService);
    await controller.listMessages(
      "agent-1",
      { deviceId: "device-1", limit: 25 },
      principal,
      { id: "request-1", headers: {} } as RequestWithPrincipal,
    );
    expect(memory.listMessages).toHaveBeenCalledWith(
      "tenant-1",
      "agent-1",
      "device-1",
      25,
      undefined,
      { principal, requestId: "request-1" },
    );
  });

  it("exports through the tenant-scoped service context instead of accepting tenant input", async () => {
    const memory = {
      exportMemory: vi.fn().mockResolvedValue({ messages: [], facts: [] }),
    };
    const controller = new MemoryController(memory as unknown as MemoryService);
    await controller.exportMemory(
      "agent-1",
      { deviceId: "4b6fbf00-4072-4ab5-b06e-a2884749d206" },
      principal,
      { id: "request-export", headers: {} } as RequestWithPrincipal,
    );
    expect(memory.exportMemory).toHaveBeenCalledWith(
      "agent-1",
      "4b6fbf00-4072-4ab5-b06e-a2884749d206",
      { principal, requestId: "request-export" },
    );
  });

  it("never accepts tenant scope from the internal voice payload", async () => {
    const memory = {
      appendMessages: vi.fn().mockResolvedValue({ accepted: 1, duplicates: 0 }),
    };
    const controller = new InternalMemoryController(memory as unknown as MemoryService);
    await controller.appendMessages({
      agentId: "ce025684-5f55-49c6-baa6-da53e11fe7ee",
      deviceId: "4b6fbf00-4072-4ab5-b06e-a2884749d206",
      configVersion: 3,
      messages: [message as MemoryMessageDto],
    });
    expect(memory.appendMessages).toHaveBeenCalledWith(
      "ce025684-5f55-49c6-baa6-da53e11fe7ee",
      "4b6fbf00-4072-4ab5-b06e-a2884749d206",
      3,
      [message],
    );
  });
});
