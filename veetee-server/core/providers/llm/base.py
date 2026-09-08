from abc import ABC, abstractmethod
from typing import List, Dict, AsyncGenerator, Tuple, Optional

class BaseLLM(ABC):
    async def correct_transcript(self, transcript: str) -> str:
        """Optionally clean up a final ASR transcript before the AI turn."""
        return transcript

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
