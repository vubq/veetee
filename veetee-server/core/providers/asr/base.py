from abc import ABC, abstractmethod
from typing import Optional

class BaseASR(ABC):
    @abstractmethod
    async def start(self):
        """Initializes and connects the streaming ASR session."""
        pass

    @abstractmethod
    async def send_audio(self, pcm_bytes: bytes, capture_generation: Optional[int] = None):
        """Streams raw PCM audio chunks into the ASR engine."""
        pass

    def invalidate_capture(self, capture_generation: int):
        """Invalidate buffered/callback work from older microphone captures."""
        return None

    async def finalize(self):
        """Flushes buffered audio while keeping the streaming ASR session open."""
        pass

    @abstractmethod
    async def stop(self):
        """Stops the ASR session and releases resources."""
        pass
