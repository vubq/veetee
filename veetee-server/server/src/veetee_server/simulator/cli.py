"""Command-line interface (CLI) for Veetee Device Simulator."""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from veetee_server.simulator.client import (
    SimulatorConfig,
    VeeteeDeviceSimulator,
    load_golden_contract,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("veetee_simulator")


def build_parser() -> argparse.ArgumentParser:
    """Constructs the CLI argument parser for Veetee Device Simulator."""
    parser = argparse.ArgumentParser(
        description="Veetee Device Simulator CLI - OTA discovery and audio protocol testing"
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080",
        help="Base server URL (e.g., http://127.0.0.1:8080 or ws://127.0.0.1:8080/api/v1/devices/ws)",
    )
    parser.add_argument(
        "--device-id",
        default="sim-device-001",
        help="Device-Id header value",
    )
    parser.add_argument(
        "--client-id",
        default="sim-client-001",
        help="Client-Id header value",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("VEETEE_DEVICE_GATEWAY_TOKEN", ""),
        help="Authorization gateway Bearer token",
    )
    parser.add_argument(
        "--protocol-version",
        type=int,
        choices=[1, 2, 3],
        default=1,
        help="Audio binary protocol version (1, 2, or 3)",
    )
    parser.add_argument(
        "--ota-check",
        action="store_true",
        help="Perform OTA discovery check before WebSocket connection",
    )
    parser.add_argument(
        "--send-hello",
        action="store_true",
        help="Send protocol hello frame after connecting",
    )
    parser.add_argument(
        "--send-listen",
        action="store_true",
        help="Send listen start frame after hello",
    )
    parser.add_argument(
        "--golden-vector",
        default="",
        help="Golden audio vector file name to send (e.g., audio_v1_golden.json)",
    )
    parser.add_argument(
        "--send-goodbye",
        action="store_true",
        help="Send goodbye frame before disconnecting",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run full automated demo (OTA -> WS -> Hello -> Listen -> Audio -> Goodbye)",
    )
    return parser


async def async_main(args: argparse.Namespace) -> int:
    """Asynchronous CLI execution workflow."""
    if args.protocol_version not in (1, 2, 3):
        logger.error("Invalid protocol version %s", args.protocol_version)
        return 1

    config = SimulatorConfig(
        server_url=args.url,
        device_id=args.device_id,
        client_id=args.client_id,
        token=args.token,
        protocol_version=args.protocol_version,
    )
    sim = VeeteeDeviceSimulator(config)

    if args.ota_check or args.demo:
        logger.info("Executing OTA discovery check at %s...", config.server_url)
        try:
            ota_res = await sim.ota_check()
            safe_ota = dict(ota_res)
            websocket_info = safe_ota.get("websocket")
            if isinstance(websocket_info, dict):
                safe_ota["websocket"] = {**websocket_info, "token": "[REDACTED]"}
            logger.info("OTA check response received successfully: %s", json.dumps(safe_ota))
        except Exception as exc:
            logger.error("OTA check failed: %s", exc)
            return 1

    if (
        args.demo
        or args.send_hello
        or args.send_listen
        or args.golden_vector
        or args.send_goodbye
    ):
        logger.info("Connecting WebSocket to %s...", config.server_url)
        try:
            await sim.connect_ws()
            logger.info("WebSocket connected.")

            if args.demo or args.send_hello:
                logger.info("Sending Hello frame...")
                hello_res = await sim.send_hello()
                logger.info("Received Hello response: %s", hello_res)

            if args.send_listen and not args.demo:
                logger.info("Sending Listen start frame...")
                await sim.send_listen(state="start", mode="auto")
                logger.info("Listen start frame sent.")

            golden_name = args.golden_vector
            if args.demo and not golden_name:
                golden_name = f"audio_v{args.protocol_version}_golden.json"

            raw_vectors: list[bytes] = []
            if golden_name:
                logger.info("Loading golden audio vector %s...", golden_name)
                contract_data = load_golden_contract(golden_name)
                vectors: list[dict[str, Any]] = contract_data.get("vectors", [])
                for vec in vectors:
                    hex_str: str = vec.get("hex_payload", "")
                    if hex_str:
                        raw_bytes = bytes.fromhex(hex_str)
                        raw_vectors.append(raw_bytes)
                        logger.info(
                            "Sending golden vector frame '%s' (%d bytes)...",
                            vec.get("name"),
                            len(raw_bytes),
                        )
                        if not args.demo:
                            await sim.send_audio_frame(raw_bytes)

            if args.demo:
                if not raw_vectors:
                    raise ValueError("Demo golden vector contains no audio frame")
                logger.info("Running complete listen/audio/STT/TTS turn...")
                events = await sim.run_turn(raw_vectors[0])
                audio_count = sum(not isinstance(event, dict) for event in events)
                logger.info("Validated complete fake-AI turn with %d audio packets.", audio_count)

            if args.demo or args.send_goodbye:
                logger.info("Sending Goodbye frame...")
                goodbye_res = await sim.send_goodbye()
                logger.info("Received Goodbye response: %s", goodbye_res)

        except Exception as exc:
            logger.error("WebSocket workflow failed: %s", exc)
            return 1
        finally:
            await sim.close()

    logger.info("Simulator completed successfully.")
    return 0


def main() -> None:
    """CLI main entrypoint."""
    parser = build_parser()
    args = parser.parse_args()
    ret = asyncio.run(async_main(args))
    sys.exit(ret)


if __name__ == "__main__":
    main()
