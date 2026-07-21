import { BadRequestException } from "@nestjs/common";

const conversationNumberBounds = {
  firstInputSeconds: [3, 300],
  betweenTurnsSeconds: [3, 600],
  closingGraceSeconds: [0.5, 60],
  maxSessionSeconds: [10, 3_600],
  totalTurnSeconds: [5, 60],
  admissionSeconds: [0.1, 5],
  plannerSeconds: [0.5, 15],
  llmSeconds: [1, 45],
  ttsSeconds: [1, 30],
  mcpSeconds: [0.5, 30],
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function validateAgentDraftConfig(config: Record<string, unknown>): void {
  const conversation = config.conversation;
  if (conversation === undefined) return;
  if (!isRecord(conversation)) {
    throw new BadRequestException("Agent conversation config must be an object");
  }
  for (const [field, [minimum, maximum]] of Object.entries(conversationNumberBounds)) {
    const value = conversation[field];
    if (value === undefined) continue;
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < minimum ||
      value > maximum
    ) {
      throw new BadRequestException(
        `Agent conversation ${field} must be between ${minimum} and ${maximum}`,
      );
    }
  }
  const goodbye = conversation.timeoutGoodbye;
  if (
    goodbye !== undefined &&
    (typeof goodbye !== "string" || !goodbye.trim() || goodbye.length > 240)
  ) {
    throw new BadRequestException(
      "Agent conversation timeoutGoodbye must contain 1 to 240 characters",
    );
  }
}
