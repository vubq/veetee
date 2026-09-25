"""Persistent management state for assistants, paired devices and runtime overrides.

Secrets never appear in public snapshots. State is written atomically with mode 0600.
Pending six-digit activation requests intentionally live in memory and expire quickly.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class ManagementStore:
    MAX_DEVICE_ID_LEN = 128
    MAX_CLIENT_ID_LEN = 128
    MAX_METADATA_VALUE_LEN = 128
    GROQ_TOKEN_LIMIT_PREFIX = "groq.token_limit."
    MAX_GROQ_TOKEN_LIMIT = 10**12

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
        self._groq_reservations: dict[int, dict[str, Any]] = {}
        self._groq_reservation_ids = itertools.count(1)
        self._state = self._load()
        self._ensure_default_assistant()

    def _blank(self) -> dict[str, Any]:
        return {
            "version": 1,
            "assistants": {},
            "devices": {},
            "runtime": {},
            "groq_credentials": {},
        }

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
        data.setdefault("groq_credentials", {})
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
            credentials = self._state.get("groq_credentials", {})
            if isinstance(credentials, dict):
                for usage in credentials.values():
                    if isinstance(usage, dict):
                        self._roll_groq_day_locked(usage)
            groq_credentials = copy.deepcopy(credentials)
        output: dict[str, Any] = {}
        for key, value in runtime.items():
            if self._is_secret_key(key):
                row = {
                    "configured": bool(value),
                    "masked": self._mask_secret(str(value or "")),
                }
                if key.startswith("GROQ_API_KEY_"):
                    usage = groq_credentials.get(key) or {}
                    used = max(0, int(usage.get("used_tokens") or 0))
                    limit = max(0, int(usage.get("token_limit") or 0))
                    row["token_usage"] = {
                        "period": "day",
                        "day": str(usage.get("usage_day") or self._groq_usage_day()),
                        "used": used,
                        "limit": limit,
                        "remaining": (
                            max(0, limit - used) if limit > 0 else None
                        ),
                    }
                output[key] = row
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
    def _groq_env_key_allowed(key: str) -> bool:
        key = str(key or "").strip()
        if not key.startswith("GROQ_API_KEY_"):
            return False
        suffix = key[len("GROQ_API_KEY_"):]
        return bool(suffix) and len(suffix) <= 64 and all(
            char.isalnum() or char == "_" for char in suffix
        )

    @classmethod
    def parse_groq_token_limit(cls, value: Any) -> int:
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            raise ValueError("Groq token limit must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Groq token limit must be an integer") from exc
        if parsed < 0 or parsed > cls.MAX_GROQ_TOKEN_LIMIT:
            raise ValueError(
                f"Groq token limit must be between 0 and {cls.MAX_GROQ_TOKEN_LIMIT}"
            )
        return parsed

    @classmethod
    def runtime_key_allowed(cls, key: str) -> bool:
        key = str(key or "").strip()
        if cls._groq_env_key_allowed(key):
            return True
        if key.startswith(cls.GROQ_TOKEN_LIMIT_PREFIX):
            env_key = key[len(cls.GROQ_TOKEN_LIMIT_PREFIX):]
            return cls._groq_env_key_allowed(env_key)
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

    @staticmethod
    def _groq_secret_fingerprint(secret: str) -> str:
        return hashlib.sha256(str(secret or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _groq_usage_day() -> str:
        # Provider daily quotas are tracked in a stable UTC calendar bucket.
        return datetime.now(timezone.utc).date().isoformat()

    def _roll_groq_day_locked(self, row: dict[str, Any]) -> bool:
        today = self._groq_usage_day()
        stored_day = str(row.get("usage_day") or "")
        if not stored_day:
            # Existing counters were introduced today; preserve them during
            # the one-time migration from lifetime to daily accounting.
            row["usage_day"] = today
            return True
        if stored_day == today:
            return False
        row["usage_day"] = today
        row["used_tokens"] = 0
        row["updated_at"] = int(time.time())
        return True

    def _drop_groq_reservations_locked(self, env_key: str) -> None:
        self._groq_reservations = {
            reservation_id: row
            for reservation_id, row in self._groq_reservations.items()
            if row.get("env_key") != env_key
        }

    def _ensure_groq_credential_locked(
        self,
        env_key: str,
        secret: str,
    ) -> tuple[dict[str, Any], bool]:
        if not self._groq_env_key_allowed(env_key):
            raise ValueError(f"invalid Groq key name: {env_key}")
        fingerprint = self._groq_secret_fingerprint(secret)
        credentials = self._state.setdefault("groq_credentials", {})
        existing = credentials.get(env_key)
        if (
            isinstance(existing, dict)
            and existing.get("secret_fingerprint") == fingerprint
            and existing.get("instance_id")
        ):
            return existing, False
        now = int(time.time())
        row = {
            "instance_id": uuid.uuid4().hex,
            "secret_fingerprint": fingerprint,
            "used_tokens": 0,
            "token_limit": 0,
            "usage_day": self._groq_usage_day(),
            "created_at": now,
            "updated_at": now,
        }
        credentials[env_key] = row
        self._drop_groq_reservations_locked(env_key)
        return row, True

    def ensure_groq_credential(self, env_key: str, secret: str) -> dict[str, Any]:
        """Ensure one usage identity for this exact credential value.

        A changed secret, or a key that was deleted and later re-added, gets a
        fresh instance with today's usage=0 and no inherited daily limit.
        """
        secret = str(secret or "").strip()
        if not secret:
            raise ValueError("Groq key secret is required")
        with self._lock:
            row, changed = self._ensure_groq_credential_locked(env_key, secret)
            rolled = self._roll_groq_day_locked(row)
            if changed or rolled:
                self._write()
            return {
                "instance_id": str(row["instance_id"]),
                "used_tokens": max(0, int(row.get("used_tokens") or 0)),
                "token_limit": max(0, int(row.get("token_limit") or 0)),
            }

    def groq_budget_snapshot(
        self,
        env_key: str,
        instance_id: str = "",
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._state.get("groq_credentials", {}).get(str(env_key))
            if not isinstance(row, dict):
                return None
            if instance_id and str(row.get("instance_id") or "") != str(instance_id):
                return None
            self._roll_groq_day_locked(row)
            used = max(0, int(row.get("used_tokens") or 0))
            limit = max(0, int(row.get("token_limit") or 0))
            reserved = sum(
                max(0, int(item.get("estimated_tokens") or 0))
                for item in self._groq_reservations.values()
                if item.get("env_key") == env_key
                and item.get("instance_id") == row.get("instance_id")
            )
            return {
                "instance_id": str(row.get("instance_id") or ""),
                "used_tokens": used,
                "token_limit": limit,
                "reserved_tokens": reserved,
                "remaining_tokens": (
                    max(0, limit - used - reserved) if limit > 0 else None
                ),
            }

    def try_reserve_groq_tokens(
        self,
        env_key: str,
        instance_id: str,
        estimated_tokens: int,
    ) -> Optional[int]:
        estimated = max(1, int(estimated_tokens))
        with self._lock:
            row = self._state.get("groq_credentials", {}).get(str(env_key))
            if (
                not isinstance(row, dict)
                or str(row.get("instance_id") or "") != str(instance_id)
            ):
                return None
            self._roll_groq_day_locked(row)
            used = max(0, int(row.get("used_tokens") or 0))
            limit = max(0, int(row.get("token_limit") or 0))
            reserved = sum(
                max(0, int(item.get("estimated_tokens") or 0))
                for item in self._groq_reservations.values()
                if item.get("env_key") == env_key
                and item.get("instance_id") == instance_id
            )
            if limit > 0 and used + reserved + estimated > limit:
                return None
            reservation_id = next(self._groq_reservation_ids)
            self._groq_reservations[reservation_id] = {
                "env_key": env_key,
                "instance_id": instance_id,
                "estimated_tokens": estimated,
            }
            return reservation_id

    def settle_groq_token_reservation(
        self,
        reservation_id: Optional[int],
        *,
        outcome: str,
        actual_tokens: int = 0,
    ) -> int:
        if reservation_id is None:
            return 0
        if outcome not in {"ok", "rejected", "uncertain"}:
            raise ValueError(f"invalid Groq token settlement: {outcome}")
        with self._lock:
            reserved = self._groq_reservations.pop(int(reservation_id), None)
            if not reserved or outcome == "rejected":
                return 0
            credentials = self._state.setdefault("groq_credentials", {})
            row = credentials.get(str(reserved.get("env_key") or ""))
            if (
                not isinstance(row, dict)
                or str(row.get("instance_id") or "")
                != str(reserved.get("instance_id") or "")
            ):
                return 0
            self._roll_groq_day_locked(row)
            estimated = max(0, int(reserved.get("estimated_tokens") or 0))
            actual = max(0, int(actual_tokens or 0))
            charged = actual if outcome == "ok" and actual > 0 else estimated
            if charged <= 0:
                return 0
            row["used_tokens"] = max(
                0, int(row.get("used_tokens") or 0)
            ) + charged
            row["updated_at"] = int(time.time())
            return charged

    def flush(self) -> None:
        """Persist the current in-memory management state atomically."""
        with self._lock:
            self._write()

    def groq_state_raw(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state.get("groq_credentials", {}))

    def restore_groq_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._state["groq_credentials"] = copy.deepcopy(dict(state or {}))
            self._groq_reservations.clear()
            self._write()

    def update_runtime(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("runtime changes must be an object")
        normalized = {str(key).strip(): value for key, value in changes.items()}
        for key in normalized:
            if not self.runtime_key_allowed(key):
                raise ValueError(f"runtime setting is not allowed: {key}")
            if key.startswith(self.GROQ_TOKEN_LIMIT_PREFIX):
                self.parse_groq_token_limit(normalized[key])

        with self._lock:
            runtime = self._state.setdefault("runtime", {})
            credentials = self._state.setdefault("groq_credentials", {})

            # Credential identity is applied before limits so one PATCH may add
            # a new key and configure its fresh daily budget atomically.
            for key, value in normalized.items():
                if not self._groq_env_key_allowed(key):
                    continue
                if value is None or value == "":
                    runtime.pop(key, None)
                    credentials.pop(key, None)
                    self._drop_groq_reservations_locked(key)
                    continue
                secret = str(value).strip()
                runtime[key] = secret
                self._ensure_groq_credential_locked(key, secret)

            for key, value in normalized.items():
                if not key.startswith(self.GROQ_TOKEN_LIMIT_PREFIX):
                    continue
                env_key = key[len(self.GROQ_TOKEN_LIMIT_PREFIX):]
                secret = str(runtime.get(env_key) or "").strip()
                if not secret:
                    raise ValueError(
                        f"configure {env_key} before setting its token limit"
                    )
                row, _changed = self._ensure_groq_credential_locked(
                    env_key, secret
                )
                row["token_limit"] = self.parse_groq_token_limit(value)
                row["updated_at"] = int(time.time())

            for key, value in normalized.items():
                if (
                    self._groq_env_key_allowed(key)
                    or key.startswith(self.GROQ_TOKEN_LIMIT_PREFIX)
                ):
                    continue
                if value is None or value == "":
                    runtime.pop(key, None)
                else:
                    runtime[key] = value
            self._write()
        return self.runtime_public()
