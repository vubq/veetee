from abc import ABC, abstractmethod
from typing import List, Dict, AsyncGenerator, Tuple, Optional

class BaseLLM(ABC):
    async def correct_transcript(self, transcript: str) -> str:
        """Optionally clean up a final ASR transcript before the AI turn."""
        return transcript


    async def generate_greetings(self, count: int = 3) -> List[str]:
        """Generate short wake greetings that match the active assistant persona."""
        return []

    async def generate_goodbye(
        self,
        messages: List[Dict[str, str]],
        *,
        reason: str,
        user_text: str = "",
    ) -> str:
        """Generate a contextual goodbye that matches the active assistant persona."""
        return ""

    async def classify_end_intent(
        self,
        user_text: str,
        messages: List[Dict[str, str]],
    ) -> bool:
        """Return True only when the user clearly intends to end the conversation."""
        return False

    @abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, str]]
    ) -> AsyncGenerator[Tuple[str, Optional[str]], None]:
        """
        Streams generated clauses/sentences from the LLM.
        Yields (clause_text, emotion_tag_or_none).
        """
        pass
