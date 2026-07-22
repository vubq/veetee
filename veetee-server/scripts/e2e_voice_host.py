from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


SERVER_ROOT = Path(__file__).resolve().parents[1]


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_until_live(port: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/health/live"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=0.5) as response:  # noqa: S310 - fixed loopback URL
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    raise TimeoutError(f"Temporary voice server did not become live on port {port}")


def stop_server(server: subprocess.Popen[bytes]) -> None:
    if server.poll() is not None:
        return
    server.send_signal(signal.SIGINT)
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the real local voice loop against an isolated loopback server."
    )
    parser.add_argument("--server-start-timeout", type=float, default=60.0)
    args, client_args = parser.parse_known_args()
    port = reserve_port()
    environment = os.environ.copy()
    environment.update(
        {
            "VEETEE_HOST": "127.0.0.1",
            "VEETEE_PORT": str(port),
            "VEETEE_REQUIRE_DEVICE_AUTH": "false",
            "VEETEE_LLM_PREWARM": "false",
        }
    )
    server = subprocess.Popen(
        [sys.executable, "apps/voice-server/main.py"],
        cwd=SERVER_ROOT,
        env=environment,
    )
    try:
        wait_until_live(port, args.server_start_timeout)
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/e2e_voice_loop.py",
                "--url",
                f"ws://127.0.0.1:{port}/veetee/v1/",
                *client_args,
            ],
            cwd=SERVER_ROOT,
            env=environment,
            check=False,
        )
        raise SystemExit(completed.returncode)
    finally:
        stop_server(server)


if __name__ == "__main__":
    main()
