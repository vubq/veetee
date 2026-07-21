from __future__ import annotations

import uvicorn

from veetee_voice_server.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "veetee_voice_server.app:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        reload=settings.reload,
    )
