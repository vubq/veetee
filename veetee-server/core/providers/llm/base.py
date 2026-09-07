from abc import ABC, abstractmethod
from typing import List, Dict, AsyncGenerator, Tuple, Optional

class BaseLLM(ABC):
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
