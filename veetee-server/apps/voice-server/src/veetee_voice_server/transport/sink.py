from __future__ import annotations

from typing import Protocol

from veetee_voice_server.conversation.types import ConversationOutput


class ConversationSink(Protocol):
    async def emit(self, output: ConversationOutput) -> None: ...


class MemoryConversationSink:
    def __init__(self) -> None:
        self.outputs: list[ConversationOutput] = []

    async def emit(self, output: ConversationOutput) -> None:
        self.outputs.append(output)
