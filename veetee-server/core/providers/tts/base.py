from abc import ABC, abstractmethod
import asyncio
from typing import AsyncGenerator, Optional

class BaseTTS(ABC):
    @abstractmethod
    async def stream_sentence_to_opus(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None
    ) -> AsyncGenerator[bytes, None]:
        """
        Synthesizes text and yields Opus-encoded audio frames in real time.
        """
        pass
