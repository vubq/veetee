from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


class SSEDecoder:
    """Decode OpenAI-style SSE frames across arbitrary TCP chunk boundaries."""

    def __init__(self, *, max_buffer_bytes: int = 65536):
        self.buffer = bytearray()
        self.max_buffer_bytes = max_buffer_bytes

    def feed(self, chunk: bytes) -> List[str]:
        if chunk:
            self.buffer.extend(chunk)
        if len(self.buffer) > self.max_buffer_bytes:
            raise ValueError("SSE buffer exceeded limit")
        events: List[str] = []
        while True:
            boundary = self.buffer.find(b"\n\n")
            alt_boundary = self.buffer.find(b"\r\n\r\n")
            if boundary < 0 or (0 <= alt_boundary < boundary):
                boundary = alt_boundary
                separator = 4
            else:
                separator = 2
            if boundary < 0:
                break
            raw = bytes(self.buffer[:boundary])
            del self.buffer[: boundary + separator]
            lines = raw.replace(b"\r\n", b"\n").split(b"\n")
            data_lines = [line[5:].lstrip() for line in lines if line.startswith(b"data:")]
            if data_lines:
                events.append(b"\n".join(data_lines).decode("utf-8"))
        return events

    def flush(self) -> List[str]:
        if not self.buffer:
            return []
        raw = bytes(self.buffer)
        self.buffer.clear()
        lines = raw.replace(b"\r\n", b"\n").split(b"\n")
        data_lines = [line[5:].lstrip() for line in lines if line.startswith(b"data:")]
        return [b"\n".join(data_lines).decode("utf-8")] if data_lines else []


@dataclass
class NativeToolCall:
    index: int
    call_id: str = ""
    name: str = ""
    arguments_text: str = ""


class NativeToolCallAccumulator:
    def __init__(self, *, max_args_bytes: int = 4096, max_calls: int = 3):
        self.calls: Dict[int, NativeToolCall] = {}
        self.max_args_bytes = max_args_bytes
        self.max_calls = max_calls

    def add_delta(self, tool_calls: Iterable[Dict[str, Any]]) -> None:
        for raw in tool_calls or []:
            index = int(raw.get("index", 0))
            if index not in self.calls and len(self.calls) >= self.max_calls:
                raise ValueError("too many tool calls in one turn")
            call = self.calls.setdefault(index, NativeToolCall(index=index))
            call_id = raw.get("id")
            if call_id:
                if call.call_id and call.call_id != call_id:
                    raise ValueError("tool call id changed while streaming")
                call.call_id = str(call_id)
            function = raw.get("function") or {}
            name = function.get("name")
            if name:
                call.name += str(name)
            args = function.get("arguments")
            if args:
                call.arguments_text += str(args)
                if len(call.arguments_text.encode("utf-8")) > self.max_args_bytes:
                    raise ValueError("tool arguments exceeded limit")

    def finalize(self) -> List[tuple[str, str, Dict[str, Any]]]:
        ready = []
        for index in sorted(self.calls):
            call = self.calls[index]
            if not call.call_id:
                raise ValueError("tool call missing model id")
            if not call.name:
                raise ValueError("tool call missing function name")
            try:
                arguments = json.loads(call.arguments_text or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid tool arguments JSON: {exc}") from exc
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
            ready.append((call.call_id, call.name, arguments))
        return ready
