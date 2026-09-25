"""Persistent management state for assistants, paired devices and runtime overrides.

Secrets never appear in public snapshots. State is written atomically with mode 0600.
Pending six-digit activation requests intentionally live in memory and expire quickly.
"""
from __future__ import annotations

import copy
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional


class ManagementStore:
    MAX_DEVICE_ID_LEN = 128
    MAX_CLIENT_ID_LEN = 128
    MAX_METADATA_VALUE_LEN = 128

    def __init__(
        self,
        path: str,
        *,
        pending_ttl_seconds: int = 300,
        pending_max: int = 256,
        pending_per_source_per_minute: int = 20,
    ):
        self.path = Path(path)
        self.pending_ttl_seconds = max(60, int(pending_ttl_seconds))
        self.pending_max = max(1, int(pending_max))
        self.pending_per_source_per_minute = max(1, int(pending_per_source_per_minute))
        self._lock = threading.RLock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._state = self._load()
        self._ensure_default_assistant()

    def _blank(self) -> dict[str, Any]:
        return {"version": 1, "assistants": {}, "devices": {}, "runtime": {}}

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("management state must be an object")
        except FileNotFoundError:
            data = self._blank()
        data.setdefault("version", 1)
        data.setdefault("assistants", {})
        data.setdefault("devices", {})
        data.setdefault("runtime", {})
        return data

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _ensure_default_assistant(self) -> None:
        with self._lock:
            if self._state["assistants"]:
                return
            now = int(time.time())
            assistant_id = "default"
            self._state["assistants"][assistant_id] = {
                "id": assistant_id,
                "name": "VeeTee",
                "base_prompt": "",
                "voice": "",
                "model": "",
                "enabled": True,
                "created_at": now,
                "updated_at": now,
            }
            self._write()

    @staticmethod
    def _public_assistant(row: dict[str, Any]) -> dict[str, Any]:
        return {k: copy.deepcopy(v) for k, v in row.items()}

    @staticmethod
    def _public_device(row: dict[str, Any]) -> dict[str, Any]:
        return {k: copy.deepcopy(v) for k, v in row.items() if k != "credential"}

    def list_assistants(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [self._public_assistant(v) for v in self._state["assistants"].values()]
        return sorted(rows, key=lambda item: (item.get("created_at", 0), item.get("id", "")))

    def get_assistant(self, assistant_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._state["assistants"].get(str(assistant_id))
            return self._public_assistant(row) if row else None

    def create_assistant(self, *, name: str, base_prompt: str = "", voice: str = "", model: str = "") -> dict[str, Any]:
        cleaned_name = str(name or "").strip()
        if not cleaned_name:
            raise ValueError("assistant name is required")
        now = int(time.time())
        assistant_id = uuid.uuid4().hex[:12]
        row = {
            "id": assistant_id,
            "name": cleaned_name[:80],
            "base_prompt": str(base_prompt or "").strip(),
            "voice": str(voice or "").strip(),
            "model": str(model or "").strip(),
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._state["assistants"][assistant_id] = row
            self._write()
        return self._public_assistant(row)

    def update_assistant(self, assistant_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "base_prompt", "voice", "model", "enabled"}
        with self._lock:
            row = self._state["assistants"].get(str(assistant_id))
            if not row:
                raise KeyError("assistant not found")
            for key, value in changes.items():
                if key not in allowed:
                    continue
                if key == "enabled":
                    if type(value) is not bool:
                        raise ValueError("enabled must be boolean")
                    row[key] = value
                else:
                    cleaned = str(value or "").strip()
                    if key == "name" and not cleaned:
                        raise ValueError("assistant name is required")
                    row[key] = cleaned[:80] if key == "name" else cleaned
            row["updated_at"] = int(time.time())
            self._write()
            return self._public_assistant(row)

    def delete_assistant(self, assistant_id: str) -> None:
        assistant_id = str(assistant_id)
        with self._lock:
            if assistant_id not in self._state["assistants"]:
                raise KeyError("assistant not found")
            if any(d.get("assistant_id") == assistant_id and not d.get("revoked") for d in self._state["devices"].values()):
                raise ValueError("assistant still has active devices")
            if len(self._state["assistants"]) <= 1:
                raise ValueError("at least one assistant is required")
            del self._state["assistants"][assistant_id]
            self._write()

    @staticmethod
    def device_key(device_id: str, client_id: str) -> str:
        return f"{str(device_id).strip().lower()}::{str(client_id).strip().lower()}"

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [self._public_device(v) for v in self._state["devices"].values()]
        return sorted(rows, key=lambda item: (item.get("paired_at", 0), item.get("device_id", "")))

    def get_device(self, device_id: str, client_id: str) -> Optional[dict[str, Any]]:
        key = self.device_key(device_id, client_id)
        with self._lock:
            row = self._state["devices"].get(key)
            return copy.deepcopy(row) if row else None

    def authenticate_device(self, device_id: str, client_id: str, credential: str) -> Optional[dict[str, Any]]:
        row = self.get_device(device_id, client_id)
        if not row or row.get("revoked") or not credential:
            return None
        if not secrets.compare_digest(str(row.get("credential") or ""), str(credential)):
            return None
        assistant = self.get_assistant(str(row.get("assistant_id") or ""))
        if not assistant or not assistant.get("enabled", True):
            return None
        self.touch_device(device_id, client_id)
        return {"kind": "device", "device": self._public_device(row), "assistant": assistant}

    def touch_device(self, device_id: str, client_id: str) -> None:
        key = self.device_key(device_id, client_id)
        with self._lock:
            row = self._state["devices"].get(key)
            if row:
                row["last_seen_at"] = int(time.time())
                self._write()

    def update_device(self, device_id: str, client_id: str, *, name: Optional[str] = None,
                      assistant_id: Optional[str] = None, owner_id: Optional[str] = None,
                      revoked: Optional[bool] = None) -> dict[str, Any]:
        key = self.device_key(device_id, client_id)
        with self._lock:
            row = self._state["devices"].get(key)
            if not row:
                raise KeyError("device not found")
            if name is not None:
                row["name"] = str(name).strip()[:80] or row.get("device_id", "Device")
            if assistant_id is not None:
                if str(assistant_id) not in self._state["assistants"]:
                    raise ValueError("assistant not found")
                row["assistant_id"] = str(assistant_id)
            if owner_id is not None:
                cleaned_owner = str(owner_id or "").strip()
                if len(cleaned_owner) > 128:
                    raise ValueError("owner_id is too long")
                row["owner_id"] = cleaned_owner
            if revoked is not None:
                row["revoked"] = bool(revoked)
            row["updated_at"] = int(time.time())
            self._write()
            return self._public_device(row)

    def _prune_pending(self) -> None:
        now = time.time()
        for key in [k for k, v in self._pending.items() if float(v.get("expires_at", 0)) <= now]:
            self._pending.pop(key, None)

    @classmethod
    def _validate_identity(cls, device_id: str, client_id: str) -> tuple[str, str]:
        device_id = str(device_id or "").strip()
        client_id = str(client_id or "").strip()
        if not device_id or len(device_id) > cls.MAX_DEVICE_ID_LEN:
            raise ValueError("device_id is invalid")
        if not client_id or len(client_id) > cls.MAX_CLIENT_ID_LEN:
            raise ValueError("client_id is invalid")
        return device_id, client_id

    @classmethod
    def _bounded_metadata(cls, metadata: Optional[dict[str, Any]]) -> dict[str, str]:
        output: dict[str, str] = {}
        for key, value in dict(metadata or {}).items():
            name = str(key)[:64]
            if not name:
                continue
            output[name] = str(value or "")[:cls.MAX_METADATA_VALUE_LEN]
        return output

    def ensure_pending(
        self,
        device_id: str,
        client_id: str,
        metadata: Optional[dict[str, Any]] = None,
        *,
        source_key: str = "",
    ) -> dict[str, Any]:
        device_id, client_id = self._validate_identity(device_id, client_id)
        key = self.device_key(device_id, client_id)
        source_key = str(source_key or "").strip()[:128]
        with self._lock:
            self._prune_pending()
            existing = self._pending.get(key)
            if existing:
                return copy.deepcopy(existing)
            if len(self._pending) >= self.pending_max:
                raise RuntimeError("too many pending pairing requests")
            if source_key:
                cutoff = time.time() - 60.0
                recent = sum(
                    1 for item in self._pending.values()
                    if item.get("source_key") == source_key
                    and float(item.get("created_at", 0)) >= cutoff
                )
                if recent >= self.pending_per_source_per_minute:
                    raise RuntimeError("pairing rate limit exceeded")
            used = {str(item.get("code")) for item in self._pending.values()}
            code = ""
            while not code or code in used:
                code = f"{secrets.randbelow(1_000_000):06d}"
            now = time.time()
            row = {
                "device_key": key,
                "device_id": device_id,
                "client_id": client_id,
                "code": code,
                "challenge": secrets.token_hex(16),
                "created_at": int(now),
                "expires_at": int(now + self.pending_ttl_seconds),
                "approved": False,
                "metadata": self._bounded_metadata(metadata),
                "source_key": source_key,
            }
            self._pending[key] = row
            return copy.deepcopy(row)

    def list_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            self._prune_pending()
            return sorted((copy.deepcopy(v) for v in self._pending.values()), key=lambda x: x["created_at"])

    def pending_for_device(self, device_id: str, client_id: str) -> Optional[dict[str, Any]]:
        key = self.device_key(device_id, client_id)
        with self._lock:
            self._prune_pending()
            row = self._pending.get(key)
            return copy.deepcopy(row) if row else None

    def pair_code(self, code: str, assistant_id: str, *, name: str = "", owner_id: str = "") -> dict[str, Any]:
        code = str(code or "").strip()
        if len(code) != 6 or not code.isdigit():
            raise ValueError("pairing code must contain exactly 6 digits")
        with self._lock:
            self._prune_pending()
            pending = next((v for v in self._pending.values() if v.get("code") == code), None)
            if not pending:
                raise KeyError("pairing code not found or expired")
            if assistant_id not in self._state["assistants"]:
                raise ValueError("assistant not found")
            key = str(pending["device_key"])
            now = int(time.time())
            existing = self._state["devices"].get(key) or {}
            row = {
                **existing,
                "device_id": pending["device_id"],
                "client_id": pending["client_id"],
                "name": str(name or existing.get("name") or pending["device_id"]).strip()[:80],
                "assistant_id": assistant_id,
                "owner_id": str(owner_id or existing.get("owner_id") or "").strip()[:128],
                # Re-pairing is the explicit recovery path: rotate the credential
                # instead of ever re-disclosing an older bearer token.
                "credential": secrets.token_urlsafe(32),
                "credential_delivered": False,
                "paired_at": now,
                "last_seen_at": now,
                "updated_at": now,
                "revoked": False,
            }
            self._state["devices"][key] = row
            pending["approved"] = True
            pending["approved_at"] = now
            self._write()
            return self._public_device(row)

    def claim_approved_credential(
        self,
        device_id: str,
        client_id: str,
        challenge: str = "",
        *,
        source_key: str = "",
    ) -> Optional[str]:
        """Return a freshly-paired credential exactly once.

        Stock firmware persists websocket settings, so an established device
        never needs the bearer token to be re-issued on routine OTA checks.
        """
        key = self.device_key(device_id, client_id)
        with self._lock:
            self._prune_pending()
            pending = self._pending.get(key)
            row = self._state["devices"].get(key)
            if not pending or not pending.get("approved") or not row or row.get("revoked"):
                return None
            expected_source = str(pending.get("source_key") or "")
            supplied_source = str(source_key or "").strip()[:128]
            if expected_source and supplied_source and expected_source != supplied_source:
                return None
            if challenge and str(pending.get("challenge")) != str(challenge):
                return None
            if row.get("credential_delivered"):
                return None
            credential = str(row.get("credential") or "")
            if not credential:
                return None
            row["credential_delivered"] = True
            row["credential_delivered_at"] = int(time.time())
            row["updated_at"] = int(time.time())
            self._pending.pop(key, None)
            self._write()
            return credential

    def activation_approved(self, device_id: str, client_id: str, challenge: str = "") -> bool:
        pending = self.pending_for_device(device_id, client_id)
        if not pending:
            return False
        # Activation-Version 1 stock firmware POSTs {} to /activate, so the
        # device identity headers are the binding key. Newer firmware may echo
        # a challenge; when present, verify it rather than weakening that flow.
        if challenge and str(pending.get("challenge")) != str(challenge):
            return False
        row = self.get_device(device_id, client_id)
        return bool(row and not row.get("revoked"))

    def runtime_public(self) -> dict[str, Any]:
        with self._lock:
            runtime = copy.deepcopy(self._state.get("runtime", {}))
        output: dict[str, Any] = {}
        for key, value in runtime.items():
            if self._is_secret_key(key):
                output[key] = {"configured": bool(value), "masked": self._mask_secret(str(value or ""))}
            else:
                output[key] = value
        return output

    def runtime_raw(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state.get("runtime", {}))

    @staticmethod
    def _is_secret_key(key: str) -> bool:
        name = str(key or "").strip()
        return (
            name.startswith("GROQ_API_KEY_")
            or name == "HF_TOKEN"
        )

    @staticmethod
    def _mask_secret(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "••••••••"
        return value[:3] + "••••••" + value[-3:]

    @staticmethod
    def runtime_key_allowed(key: str) -> bool:
        key = str(key or "").strip()
        if key.startswith("GROQ_API_KEY_"):
            suffix = key[len("GROQ_API_KEY_"):]
            return bool(suffix) and len(suffix) <= 64 and all(
                char.isalnum() or char == "_" for char in suffix
            )
        return key in {
            "HF_TOKEN",
            "llm.model",
            "llm.speech_segmentation.min_segment_chars",
            "llm.speech_segmentation.clause_target_chars",
            "llm.speech_segmentation.clause_min_chars",
            "llm.speech_segmentation.first_clause_min_chars",
            "llm.speech_segmentation.first_clause_min_words",
            "llm.speech_segmentation.hard_max_segment_chars",
            "llm.speech_segmentation.hard_cut_search_back",
            "llm.speech_segmentation.hard_cut_search_forward",
            "llm.speech_segmentation.first_segment_min_chars",
            "llm.speech_segmentation.first_segment_min_words",
            "llm.speech_segmentation.first_soft_cut_chars",
            "llm.speech_segmentation.first_soft_cut_min_words",
            "llm.routing.headroom_pct",
            "llm.routing.max_attempts",
            "llm.routing.admission_wait_ms",
            "llm.routing.discovery_wait_ms",
            "llm.routing.discovery_max_inflight",
            "llm.routing.inflight_penalty_s",
            "llm.routing.latency_ewma_alpha",
            "llm.routing.latency_jitter_penalty",
            "tts.voice",
            "asr.device",
            "server.barge_in_policy",
            "latency.target_first_audio_ms",
            "latency.first_token_timeout_ms",
            "latency.total_turn_timeout_ms",
            "asr.min_silence_duration_ms",
            "asr.speculative_inference_enabled",
            "asr.speculative_start_silence_ms",
            "asr.speculative_min_confidence",
            "asr.speculative_llm_enabled",
            "asr.speculative_llm_min_confidence",
            "asr.min_speech_duration_ms",
            "asr.speech_start_frames",
            "asr.pre_speech_pad_ms",
            "asr.vad_threshold",
            "asr.vad_threshold_low",
            "asr.vad_end_threshold",
            "tts.send_ahead_ms",
            "tts.stream_queue_max_chunks",
            "tts.first_audio_priority_boost",
            "tts.scheduler_aging_per_second",
            "tts.admission_timeout_ms",
            "tts.speculative_prefetch_enabled",
            "memory.lookup_timeout_ms",
            "conversation.history_turns",
            "tools.max_parallel_read_only",
            "tools.max_llm_rounds_per_turn",
        }

    def update_runtime(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("runtime changes must be an object")
        with self._lock:
            runtime = self._state.setdefault("runtime", {})
            for key, value in changes.items():
                key = str(key).strip()
                if not self.runtime_key_allowed(key):
                    raise ValueError(f"runtime setting is not allowed: {key}")
                if value is None or value == "":
                    runtime.pop(key, None)
                else:
                    runtime[key] = value
            self._write()
        return self.runtime_public()
