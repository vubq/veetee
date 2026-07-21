import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { describe, expect, it, vi } from "vitest";

import type { ControlPlaneStore } from "../store/control-plane.store.js";
import {
  ConversationEventDto,
  ConversationEventsController,
} from "./conversation-events.controller.js";

const event = {
  eventId: "71b469fa-9fa1-4926-902f-004833a2473d",
  sessionId: "session_12345678",
  turnId: "session_12345678:1",
  generation: 3,
  eventType: "admission",
  payload: { disposition: "accepted", confidence: 0.9 },
  occurredAt: "2026-07-22T03:25:00.000Z",
};

describe("ConversationEventsController", () => {
  it("accepts a bounded redacted event contract", async () => {
    await expect(validate(plainToInstance(ConversationEventDto, event))).resolves.toEqual([]);
  });

  it("rejects oversized or unbounded event labels", async () => {
    const invalid = plainToInstance(ConversationEventDto, {
      ...event,
      eventType: "INVALID EVENT TYPE",
    });
    expect(await validate(invalid)).not.toEqual([]);
  });

  it("derives tenant scope in the store instead of trusting the voice payload", async () => {
    const store = {
      ingestConversationEvents: vi.fn().mockResolvedValue({ accepted: 1 }),
    } as unknown as ControlPlaneStore;
    const controller = new ConversationEventsController(store);

    await expect(
      controller.ingest({ deviceId: "4b6fbf00-4072-4ab5-b06e-a2884749d206", events: [event] }),
    ).resolves.toEqual({ accepted: 1 });
    expect(store.ingestConversationEvents).toHaveBeenCalledWith(
      "4b6fbf00-4072-4ab5-b06e-a2884749d206",
      [event],
    );
  });
});
