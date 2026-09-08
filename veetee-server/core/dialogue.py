import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class ChatMessage:
    role: str # "user", "assistant", "system"
    content: str
    timestamp: float = field(default_factory=time.time)

class DialogueContext:
    def __init__(self, max_history_turns: int = 6):
        self.max_history_turns = max_history_turns
        self.messages: List[ChatMessage] = []

    def add_user_message(self, text: str):
        if not text or not text.strip():
            return
        # Preserve unanswered/interrupted user turns. A later follow-up may
        # depend on the exact request that was interrupted.
        self.messages.append(ChatMessage(role="user", content=text.strip()))
        self._trim()

    def add_assistant_message(self, text: str):
        if not text or not text.strip():
            return
        if self.messages and self.messages[-1].role == "assistant":
            self.messages[-1].content += " " + text.strip()
        else:
            self.messages.append(ChatMessage(role="assistant", content=text.strip()))
        self._trim()

    def get_messages_for_llm(self) -> List[Dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def _trim(self):
        if len(self.messages) > self.max_history_turns * 2:
            self.messages = self.messages[-(self.max_history_turns * 2):]

    def clear(self):
        self.messages.clear()
