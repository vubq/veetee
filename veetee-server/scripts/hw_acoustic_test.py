#!/usr/bin/env python3
"""Automated acoustic hardware-in-the-loop test for VeeTee + ESP32 board.

Loop under test (all verified automatically, no human ears needed except
audible-speaker confirmation which stays manual):
  laptop speaker -> board mic -> server ASR/LLM/TTS -> board speaker/log

Checks:
  1. wake: play wake WAV -> new server session appears and/or serial shows
     "Wake word detected".
  2. qa: play question WAV -> server logs a non-empty final transcript AND
     sends TTS audio for the turn.
  3. idle (opt-in --idle): session closes by itself after idle_timeout and
     board returns to idle; then re-wake opens a fresh session.

Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED (environment not ready).

Needs: server running, sudo -n access to the serial port, pw-play audio.
Serial port is opened by at most one reader; this script owns it while
running, so do not run two copies or a manual `cat` concurrently.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

TRANSCRIPT_RE = re.compile(r"Parakeet final transcript: '(.*)'")
TTS_SENT_RE = re.compile(r"Post-ASR first TTS binary sent in|first TTS binary sent in")
WAKE_SERIAL_RE = re.compile(r"Wake word detected", re.IGNORECASE)
CONNECT_FAIL_SERIAL_RE = re.compile(
    r"Failed to connect to (server|websocket)|websocket.*failed",
    re.IGNORECASE)
SPEAKING_SERIAL_RE = re.compile(
    r"State: listening -> speaking|Set output enable to true")
DISCONNECT_SERIAL_RE = re.compile(r"Websocket disconnected", re.IGNORECASE)


def parse_transcript(line: str) -> str | None:
    """Return transcript text from a server journal line, else None."""
    match = TRANSCRIPT_RE.search(line)
    return match.group(1).strip() if match else None


def health(server: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(server.rstrip("/") + "/health", timeout=timeout) as resp:
        return json.load(resp)


def journal_since(cursor: str, timeout: float = 30.0) -> str:
    out = subprocess.run(
        ["journalctl", "--user", "-u", "veetee-server-bg.service",
         "--since", cursor, "--no-pager"],
        capture_output=True, text=True, timeout=timeout,
    )
    return out.stdout


def utcnow_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class SerialWatch:
    """Background `cat` of the serial port; thread-safe line buffer."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.lines: list[str] = []
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        subprocess.run(["sudo", "-n", "stty", "-F", self.port, str(self.baud),
                        "raw", "-echo"], check=False,
                       capture_output=True, timeout=15)
        self.proc = subprocess.Popen(
            ["sudo", "-n", "timeout", "1200", "cat", self.port],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        # Byte-at-a-time: board idles at ~6 log bytes/s, so any block read
        # larger than a line would stall the watcher for tens of seconds.
        assert self.proc and self.proc.stdout
        buf = b""
        while not self._stop.is_set():
            chunk = self.proc.stdout.read(1)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "ignore")
                with self.lock:
                    self.lines.append(text)

    def grep(self, pattern: re.Pattern, since_index: int = 0) -> list[str]:
        with self.lock:
            return [line for line in self.lines[since_index:]
                    if pattern.search(line)]

    def mark(self) -> int:
        with self.lock:
            return len(self.lines)

    def stop(self) -> None:
        self._stop.set()
        if self.proc:
            self.proc.terminate()


def play_wav(path: str, player: str = "pw-play", timeout: float = 30.0) -> bool:
    proc = subprocess.run([player, path], capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode == 0


def wait_for(predicate, timeout: float, interval: float = 2.0,
             label: str = "") -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    print(f"TIMEOUT waiting: {label}", flush=True)
    return False


def sessions(server: str) -> int | None:
    try:
        return int(health(server).get("active_sessions", -1))
    except Exception:
        return None


def run_case(args) -> int:
    results: list[tuple[str, bool, str]] = []

    # ---- pre-checks (BLOCKED when environment is not ready) ----
    try:
        status = health(args.server)
    except Exception as exc:
        print(f"BLOCKED: server unhealthy: {exc}", flush=True)
        return 2
    if status.get("readiness") != "ready":
        print(f"BLOCKED: server not ready: {status}", flush=True)
        return 2
    watch = SerialWatch(args.serial)
    try:
        watch.start()
    except Exception as exc:
        print(f"BLOCKED: cannot open serial {args.serial}: {exc}", flush=True)
        return 2
    # Board prints SystemInfo roughly every 10 s when idle; allow a full
    # window before concluding the cable/power is missing.
    if not wait_for(lambda: bool(watch.lines), timeout=15.0,
                    interval=1.0, label="first serial output"):
        print("BLOCKED: no serial output from board (cable/power?)",
              flush=True)
        watch.stop()
        return 2
    print("pre-checks OK: server ready, board talking on serial",
          flush=True)

    try:
        # ---- 0. drain stale sessions so wake detection means a FRESH
        # session opened by this run (a lingering session would fake PASS
        # while the board itself is offline) ----
        print("waiting for stale sessions to drain...", flush=True)
        if not wait_for(lambda: sessions(args.server) == 0,
                        timeout=args.drain_wait, interval=5.0,
                        label="stale session drain"):
            print("BLOCKED: stale session never closed", flush=True)
            return 2

        # ---- 1. wake ----
        mark = watch.mark()
        cursor = utcnow_stamp()
        woke = False
        fail_reason = ""
        for attempt in range(1, args.wake_tries + 1):
            print(f"wake attempt {attempt}/{args.wake_tries}", flush=True)
            if not play_wav(args.wake_wav):
                print("BLOCKED: audio player failed", flush=True)
                return 2
            woke = wait_for(
                lambda: (sessions(args.server) or 0) > 0,
                timeout=args.wake_wait, interval=2.0, label="wake")
            if not woke and watch.grep(CONNECT_FAIL_SERIAL_RE, mark):
                fail_reason = ("board woke but cannot reach server "
                               "(see serial: Failed to connect)")
                break
            if woke:
                break
            time.sleep(3)
        results.append(("wake", woke,
                        "fresh session opened" if woke
                        else (fail_reason or "no session after wake audio")))
        if not woke:
            return finish(results)
        # Wait for TRUE quiet, not a fixed delay. This single-mic board has
        # no AEC reference, so it hears its own greeting speaker output and
        # opens echo turns. Only ask when no new transcript has appeared
        # for a while; otherwise the question collides with echo audio and
        # the echo guard discards it.
        print("waiting for channel quiet "
              f"(no transcript for {args.quiet}s)...", flush=True)
        seen: set[str] = set()

        def channel_quiet() -> bool:
            nonlocal quiet_since
            found_new = False
            for line in journal_since(cursor).splitlines():
                text = parse_transcript(line)
                if text and text not in seen:
                    seen.add(text)
                    found_new = True
                    print(f"  (echo turn observed: {text!r})", flush=True)
            now = time.monotonic()
            if found_new:
                quiet_since = None
                return False
            if quiet_since is None:
                quiet_since = now
            return now - quiet_since >= args.quiet

        quiet_since: float | None = None
        if not wait_for(channel_quiet, timeout=args.quiet_timeout,
                        interval=2.0, label="quiet channel"):
            print("WARNING: channel never went quiet; continuing anyway",
                  flush=True)

        # ---- 2. question -> transcript + TTS ----
        # The single-mic board self-echoes, so one attempt may land in an
        # echo turn. Retry the question across gaps; PASS on the first
        # transcript matching --expect (or any transcript without it).
        heard = ""
        ok_stt = ok_tts = ok_spoke = False
        for attempt in range(1, args.qa_tries + 1):
            mark = watch.mark()
            cursor = utcnow_stamp()
            print(f"question attempt {attempt}/{args.qa_tries}",
                  flush=True)
            if not play_wav(args.question_wav):
                print("BLOCKED: audio player failed", flush=True)
                return 2
            transcript: list[str] = []

            def got_transcript() -> bool:
                for line in journal_since(cursor).splitlines():
                    text = parse_transcript(line)
                    if text and text not in transcript:
                        transcript.append(text)
                        print(f"  (heard: {text!r})", flush=True)
                        if not args.expect or \
                                args.expect.lower() in text.lower():
                            return True
                return False

            if not wait_for(got_transcript, timeout=args.qa_wait,
                            interval=2.0, label="matching transcript"):
                continue
            heard = transcript[-1]
            ok_stt = True
            tts_cursor = utcnow_stamp()
            ok_tts = wait_for(
                lambda: bool(TTS_SENT_RE.search(journal_since(tts_cursor))),
                timeout=30.0, interval=2.0, label="TTS binary sent")
            ok_spoke = wait_for(
                lambda: bool(watch.grep(SPEAKING_SERIAL_RE, mark)),
                timeout=30.0, interval=2.0, label="board speaking")
            break
        results.append(("stt", ok_stt, f"transcript={heard!r}"))
        results.append(("tts", ok_tts, "server sent TTS audio"))
        results.append(("board_tts", ok_spoke,
                        "board entered speaking state"))

        # ---- 3. idle close + re-wake (opt-in, slow) ----
        if args.idle and all(passed for _, passed, _ in results):
            print("waiting for idle close "
                  f"(up to {args.idle_wait}s)...", flush=True)
            closed = wait_for(lambda: sessions(args.server) == 0,
                              timeout=args.idle_wait, interval=5.0,
                              label="idle close")
            results.append(("idle_close", bool(closed),
                            "session closed by itself"))
            if closed:
                time.sleep(3)
                mark2 = watch.mark()
                play_wav(args.wake_wav)
                rewoke = wait_for(
                    lambda: (sessions(args.server) or 0) > 0
                    or bool(watch.grep(WAKE_SERIAL_RE, mark2)),
                    timeout=args.wake_wait, interval=2.0,
                    label="re-wake")
                results.append(("re_wake", rewoke,
                                "fresh session after idle close"))
    finally:
        if args.serial_log:
            try:
                with open(args.serial_log, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(watch.lines))
                print(f"serial log saved: {args.serial_log} "
                      f"({len(watch.lines)} lines)", flush=True)
            except OSError as exc:
                print(f"WARNING: cannot save serial log: {exc}",
                      flush=True)
        watch.stop()
    return finish(results)


def finish(results: list[tuple[str, bool, str]]) -> int:
    print("=== HW ACOUSTIC RESULT ===", flush=True)
    failed = False
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}",
              flush=True)
        failed = failed or not passed
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8003")
    parser.add_argument("--serial", default="/dev/ttyACM0")
    parser.add_argument("--wake-wav",
                        default="eval/audio/wake_hi_esp_en_us.wav")
    parser.add_argument("--question-wav",
                        default="eval/audio/vn_may_gio_roi.wav")
    parser.add_argument("--expect", default="",
                        help="required substring (case-insensitive) in the "
                             "final transcript, e.g. 'mấy giờ'")
    parser.add_argument("--wake-tries", type=int, default=3)
    parser.add_argument("--drain-wait", type=float, default=150.0,
                        help="max seconds to wait for stale sessions")
    parser.add_argument("--wake-wait", type=float, default=25.0)
    parser.add_argument("--quiet", type=float, default=10.0,
                        help="transcript-free seconds required before question")
    parser.add_argument("--quiet-timeout", type=float, default=120.0,
                        help="max seconds to wait for a quiet channel")
    parser.add_argument("--qa-wait", type=float, default=60.0)
    parser.add_argument("--qa-tries", type=int, default=4,
                        help="question repetitions across echo gaps")
    parser.add_argument("--idle", action="store_true",
                        help="also verify idle close + re-wake (slow)")
    parser.add_argument("--idle-wait", type=float, default=220.0)
    parser.add_argument("--serial-log", default="",
                        help="persist raw serial capture for forensics")
    args = parser.parse_args()
    try:
        return run_case(args)
    except KeyboardInterrupt:
        print("aborted", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
