"""PostgreSQL repositories for device lifecycle and OTA."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from functools import cmp_to_key
from typing import Any, cast

from psycopg.types.json import Jsonb

from veetee_server.domain.device_lifecycle import (
    BindingConflictError,
    ExpiredCodeError,
    InvalidCodeError,
    MaxAttemptsExceededError,
    ReportRateLimitError,
    canonical_activation_challenge,
    compare_semver,
    create_device_bootstrap_token,
    create_device_ws_token,
    derive_activation_code,
    hash_activation_code,
    is_device_in_cohort,
    parse_semver,
    verify_activation_code,
    verify_device_bootstrap_token,
    verify_device_ws_token,
    verify_ed25519_proof,
)

from .database import PostgresDatabase


class DeviceRepository:
    """Manages veetee_devices, activation challenges, credentials, and binding history."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get_by_device_id(self, device_id: str) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices WHERE device_id = %s",
                (device_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def get_by_id(self, id_: uuid.UUID) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices WHERE id = %s",
                (id_,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def list_by_owner(self, owner_user_id: uuid.UUID) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices WHERE owner_user_id = %s ORDER BY created_at, id",
                (owner_user_id,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices ORDER BY created_at, id",
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def get_or_create_unbound(
        self,
        device_id: str,
        client_id: str = "",
        board: str = "",
        chip: str = "",
        partition: str = "",
        firmware_version: str = "",
    ) -> dict[str, Any]:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices WHERE device_id = %s",
                (device_id,),
            ).fetchone()
            if row:
                # Update metadata and last_seen_at
                conn.execute(
                    "UPDATE veetee_devices SET "
                    "client_id = CASE WHEN status = 'unbound' "
                    "THEN COALESCE(NULLIF(%s, ''), client_id) ELSE client_id END, "
                    "board = COALESCE(NULLIF(%s, ''), board), "
                    "chip = COALESCE(NULLIF(%s, ''), chip), "
                    "partition = COALESCE(NULLIF(%s, ''), partition), "
                    "observed_firmware_version = "
                    "COALESCE(NULLIF(%s, ''), observed_firmware_version), "
                    "last_seen_at = now(), updated_at = now() "
                    "WHERE device_id = %s",
                    (client_id, board, chip, partition, firmware_version, device_id),
                )
                updated_row = conn.execute(
                    "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                    "board, chip, partition, current_firmware_version, auto_update, "
                    "channel, cohort, "
                    "last_seen_at, created_at, updated_at, version "
                    "FROM veetee_devices WHERE device_id = %s",
                    (device_id,),
                ).fetchone()
                assert updated_row is not None
                return self._row_to_dict(updated_row)

            # Insert new unbound device
            new_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO veetee_devices "
                "(id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, observed_firmware_version, last_seen_at) "
                "VALUES (%s, NULL, NULL, %s, %s, '', 'unbound', %s, %s, %s, %s, now())",
                (new_id, device_id, client_id, board, chip, partition, firmware_version),
            )
            inserted_row = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices WHERE id = %s",
                (new_id,),
            ).fetchone()
            assert inserted_row is not None
            return self._row_to_dict(inserted_row)

    def provision_enrollment(
        self,
        actor_user_id: uuid.UUID,
        device_id: str,
        client_id: str | None,
        public_key: str,
        board: str,
        chip: str,
        partition: str,
    ) -> dict[str, Any]:
        """Creates or replaces an unbound enrollment without storing private material."""
        with self.database.connection() as conn:
            existing = conn.execute(
                "SELECT status FROM veetee_devices WHERE device_id = %s FOR UPDATE", (device_id,)
            ).fetchone()
            if existing is not None and existing[0] == "bound":
                raise BindingConflictError("Bound device enrollment cannot be replaced")
            conn.execute(
                "INSERT INTO veetee_devices "
                "(id, owner_user_id, agent_id, device_id, client_id, alias, status, board, chip, "
                "partition) VALUES (%s, NULL, NULL, %s, %s, '', 'unbound', %s, %s, %s) "
                "ON CONFLICT (device_id) DO UPDATE SET client_id = EXCLUDED.client_id, "
                "board = EXCLUDED.board, chip = EXCLUDED.chip, partition = EXCLUDED.partition, "
                "updated_at = now()",
                (uuid.uuid4(), device_id, client_id or "", board, chip, partition),
            )
            conn.execute(
                "INSERT INTO veetee_device_enrollments "
                "(device_id, client_id, ed25519_public_key, provisioned_by) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (device_id) DO UPDATE SET "
                "client_id = EXCLUDED.client_id, ed25519_public_key = EXCLUDED.ed25519_public_key, "
                "provisioned_by = EXCLUDED.provisioned_by, created_at = now(), revoked_at = NULL",
                (device_id, client_id, public_key, actor_user_id),
            )
            conn.execute(
                "UPDATE veetee_device_activation_challenges SET consumed_at = now() "
                "WHERE device_id = %s AND consumed_at IS NULL",
                (device_id,),
            )
            conn.execute(
                "INSERT INTO veetee_audit_events "
                "(id, actor_user_id, action, resource_type, resource_id, metadata) "
                "VALUES (%s, %s, 'device.provision', 'device', %s, '{}'::jsonb)",
                (uuid.uuid4(), actor_user_id, device_id),
            )
            return self._row_to_dict(self._select_device(conn, device_id))

    def authenticate_observe_and_rotate(
        self,
        device_id: str,
        client_id: str,
        token: str,
        secret: str,
        ttl_seconds: int,
        board: str,
        chip: str,
        partition: str,
        observed_version: str,
        min_interval_seconds: float,
    ) -> tuple[dict[str, Any], str]:
        """Authenticates a bound discovery before accepting telemetry or rotating."""
        ws_claims = verify_device_ws_token(token, secret)
        bootstrap_claims = verify_device_bootstrap_token(token, secret)
        claims = ws_claims or bootstrap_claims
        if (
            claims is None
            or claims.get("device_id") != device_id
            or claims.get("client_id") != client_id
        ):
            raise PermissionError("Valid active device credential required")
        try:
            jti = uuid.UUID(cast(str, claims["jti"]))
        except (KeyError, ValueError) as exc:
            raise PermissionError("Valid active device credential required") from exc

        with self.database.connection() as conn:
            device = conn.execute(
                "SELECT status, client_id, last_discovery_at FROM veetee_devices "
                "WHERE device_id = %s FOR UPDATE",
                (device_id,),
            ).fetchone()
            if device is None or device[0] != "bound" or device[1] != client_id:
                raise PermissionError("Device identity is not bound")
            credential = conn.execute(
                "SELECT kind FROM veetee_device_credentials "
                "WHERE jti = %s AND device_id = %s AND client_id = %s "
                "AND status = 'active' AND expires_at > now() FOR UPDATE",
                (jti, device_id, client_id),
            ).fetchone()
            if credential is None:
                raise PermissionError("Device credential is revoked or expired")
            kind = cast(str, credential[0])
            if (ws_claims is not None and kind != "ws") or (
                bootstrap_claims is not None and kind not in {"bootstrap", "recovery"}
            ):
                raise PermissionError("Credential type does not match token audience")
            last_discovery = cast(datetime | None, device[2])
            if (
                last_discovery is not None
                and (datetime.now(UTC) - last_discovery).total_seconds() < min_interval_seconds
            ):
                raise RuntimeError("Device discovery rate limit exceeded")

            conn.execute(
                "UPDATE veetee_devices SET board = COALESCE(NULLIF(%s, ''), board), "
                "chip = COALESCE(NULLIF(%s, ''), chip), "
                "partition = COALESCE(NULLIF(%s, ''), partition), "
                "observed_firmware_version = COALESCE(NULLIF(%s, ''), observed_firmware_version), "
                "last_seen_at = now(), last_discovery_at = now(), updated_at = now() "
                "WHERE device_id = %s",
                (board, chip, partition, observed_version, device_id),
            )
            new_jti = uuid.uuid4()
            new_token = create_device_ws_token(device_id, client_id, new_jti, ttl_seconds, secret)
            token_hash = hashlib.sha256(new_token.encode()).hexdigest()
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
            conn.execute(
                "INSERT INTO veetee_device_credentials "
                "(id, device_id, client_id, jti, token_hash, kind, status, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, 'ws', 'active', %s)",
                (
                    uuid.uuid4(),
                    device_id,
                    client_id,
                    new_jti,
                    token_hash,
                    expires_at,
                ),
            )
            conn.execute(
                "UPDATE veetee_device_credentials SET status = 'revoked', revoked_at = now() "
                "WHERE device_id = %s AND client_id = %s AND status = 'active' AND jti <> %s",
                (device_id, client_id, new_jti),
            )
            return self._row_to_dict(self._select_device(conn, device_id)), new_token

    def recover_client(
        self,
        actor_user_id: uuid.UUID,
        device_id: str,
        client_id: str,
        *,
        is_admin: bool,
        ttl_seconds: int,
        secret: str,
    ) -> tuple[dict[str, Any], str]:
        """Assigns the first M5 Client-Id to a retained M4 owner."""
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT owner_user_id, status FROM veetee_devices WHERE device_id = %s FOR UPDATE",
                (device_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Device not found")
            if row[1] != "recovery_required":
                raise BindingConflictError("Device does not require recovery")
            if not is_admin and row[0] != actor_user_id:
                raise BindingConflictError("Device belongs to another owner")
            conn.execute(
                "UPDATE veetee_devices SET client_id = %s, status = 'bound', "
                "updated_at = now(), version = version + 1 WHERE device_id = %s",
                (client_id, device_id),
            )
            token = DeviceCredentialRepository._insert_bootstrap_credential(
                conn, device_id, client_id, ttl_seconds, secret, "recovery"
            )
            conn.execute(
                "INSERT INTO veetee_device_binding_history "
                "(id, device_id, user_id, action, actor_user_id, metadata) "
                "VALUES (%s, %s, %s, 'rebound', %s, %s)",
                (
                    uuid.uuid4(),
                    device_id,
                    row[0],
                    actor_user_id,
                    Jsonb({"reason": "m4_client_recovery", "client_id": client_id}),
                ),
            )
            conn.execute(
                "INSERT INTO veetee_audit_events "
                "(id, actor_user_id, action, resource_type, resource_id, metadata) "
                "VALUES (%s, %s, 'device.recover_client', 'device', %s, %s)",
                (uuid.uuid4(), actor_user_id, device_id, Jsonb({"client_id": client_id})),
            )
            return self._row_to_dict(self._select_device(conn, device_id)), token

    def bind_device(
        self,
        user_id: uuid.UUID,
        device_id: str,
        code: str,
        secret: str,
        alias: str = "",
        agent_id: uuid.UUID | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Atomic activation verification and binding transaction."""
        with self.database.connection() as conn:
            request_hash = self._operation_hash(
                {
                    "device_id": device_id,
                    "code": code,
                    "alias": alias,
                    "agent_id": str(agent_id) if agent_id else None,
                }
            )
            replay = self._get_operation(
                conn, idempotency_key, user_id, "device.bind", request_hash
            )
            if replay is not None:
                return replay
            if (
                agent_id is not None
                and conn.execute(
                    "SELECT 1 FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                    (agent_id, user_id),
                ).fetchone()
                is None
            ):
                raise BindingConflictError("Agent does not belong to binding owner")
            dev_row = conn.execute(
                "SELECT id, owner_user_id, status FROM veetee_devices "
                "WHERE device_id = %s FOR UPDATE",
                (device_id,),
            ).fetchone()
            if dev_row and dev_row[1] == user_id and dev_row[2] == "bound":
                result = self._select_device(conn, device_id)
                response = self._row_to_dict(result)
                self._save_operation(
                    conn,
                    idempotency_key,
                    user_id,
                    "device.bind",
                    request_hash,
                    response,
                    device_id,
                )
                return response
            if dev_row and dev_row[1] is not None and dev_row[1] != user_id:
                raise BindingConflictError("Device is already bound to another owner")

            # 1. Fetch challenge
            ch_row = conn.execute(
                "SELECT id, code_hash, salt, expires_at, attempts, max_attempts, consumed_at "
                "FROM veetee_device_activation_challenges "
                "WHERE device_id = %s AND consumed_at IS NULL AND expires_at > now() "
                "AND (proof_verified_at IS NOT NULL OR NOT EXISTS ("
                "SELECT 1 FROM veetee_device_enrollments e "
                "WHERE e.device_id = veetee_device_activation_challenges.device_id "
                "AND e.revoked_at IS NULL)) "
                "ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
                (device_id,),
            ).fetchone()
            if not ch_row:
                raise ExpiredCodeError("No active or unexpired activation code found for device")

            ch_id = cast(uuid.UUID, ch_row[0])
            code_hash = cast(str, ch_row[1])
            salt = cast(str, ch_row[2])
            attempts = cast(int, ch_row[4])
            max_attempts = cast(int, ch_row[5])
            if attempts >= max_attempts:
                raise MaxAttemptsExceededError("Maximum activation attempts exceeded")

            if not verify_activation_code(code, code_hash, secret, salt):
                conn.execute(
                    "UPDATE veetee_device_activation_challenges "
                    "SET attempts = attempts + 1 WHERE id = %s",
                    (ch_id,),
                )
                conn.commit()
                if attempts + 1 >= max_attempts:
                    raise MaxAttemptsExceededError("Maximum activation attempts reached")
                raise InvalidCodeError("Invalid activation code")

            # Mark challenge consumed
            conn.execute(
                "UPDATE veetee_device_activation_challenges SET consumed_at = now() WHERE id = %s",
                (ch_id,),
            )
            if dev_row:
                cur_owner, cur_status = dev_row[1], dev_row[2]
                if cur_owner is not None and cur_owner != user_id:
                    raise BindingConflictError("Device is already bound to another owner")
                if cur_owner == user_id and cur_status == "bound":
                    # Idempotent re-bind by same user
                    pass

            # 3. Update device
            dev_id = dev_row[0] if dev_row else uuid.uuid4()
            if dev_row:
                conn.execute(
                    "UPDATE veetee_devices SET "
                    "owner_user_id = %s, status = 'bound', "
                    "alias = COALESCE(NULLIF(%s, ''), alias), "
                    "agent_id = COALESCE(%s, agent_id), updated_at = now(), version = version + 1 "
                    "WHERE device_id = %s",
                    (user_id, alias, agent_id, device_id),
                )
            else:
                conn.execute(
                    "INSERT INTO veetee_devices "
                    "(id, owner_user_id, agent_id, device_id, alias, status, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, 'bound', now())",
                    (dev_id, user_id, agent_id, device_id, alias),
                )

            # 4. Record binding/rebinding history and owner transition policy.
            previous = conn.execute(
                "SELECT user_id FROM veetee_device_binding_history "
                "WHERE device_id = %s AND action = 'unbound' "
                "ORDER BY created_at DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            history_action = "rebound" if previous is not None else "bound"
            conn.execute(
                "INSERT INTO veetee_device_binding_history "
                "(id, device_id, user_id, action, actor_user_id, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    uuid.uuid4(),
                    device_id,
                    user_id,
                    history_action,
                    user_id,
                    Jsonb(
                        {
                            "alias": alias,
                            "previous_owner": str(previous[0]) if previous else None,
                            "owner_changed": bool(previous and previous[0] != user_id),
                        }
                    ),
                ),
            )

            # 5. Record audit event
            conn.execute(
                "INSERT INTO veetee_audit_events "
                "(id, actor_user_id, action, resource_type, resource_id, metadata) "
                "VALUES (%s, %s, 'device.bind', 'device', %s, %s)",
                (uuid.uuid4(), user_id, device_id, Jsonb({"alias": alias})),
            )

            res = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices WHERE device_id = %s",
                (device_id,),
            ).fetchone()
            assert res is not None
            response = self._row_to_dict(res)
            self._save_operation(
                conn,
                idempotency_key,
                user_id,
                "device.bind",
                request_hash,
                response,
                device_id,
            )
            return response

    def unbind_device(
        self,
        actor_user_id: uuid.UUID,
        device_id: str,
        *,
        is_admin: bool = False,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Atomic unbind transaction: clears owner, revokes credentials, logs audit."""
        with self.database.connection() as conn:
            request_hash = self._operation_hash({"device_id": device_id, "is_admin": is_admin})
            replay = self._get_operation(
                conn, idempotency_key, actor_user_id, "device.unbind", request_hash
            )
            if replay is not None:
                return replay
            dev_row = conn.execute(
                "SELECT id, owner_user_id, status FROM veetee_devices "
                "WHERE device_id = %s FOR UPDATE",
                (device_id,),
            ).fetchone()

            if not dev_row:
                raise ValueError("Device not found")

            cur_owner = dev_row[1]
            if cur_owner is None:
                response = self._row_to_dict(self._select_device(conn, device_id))
                self._save_operation(
                    conn,
                    idempotency_key,
                    actor_user_id,
                    "device.unbind",
                    request_hash,
                    response,
                    device_id,
                )
                return response
            if not is_admin and cur_owner != actor_user_id:
                raise BindingConflictError("Device belongs to another owner")

            # 1. Update device state
            conn.execute(
                "UPDATE veetee_devices SET "
                "owner_user_id = NULL, status = 'unbound', agent_id = NULL, "
                "updated_at = now(), version = version + 1 "
                "WHERE device_id = %s",
                (device_id,),
            )

            # 2. Revoke all active credentials for device
            conn.execute(
                "UPDATE veetee_device_credentials SET status = 'revoked', revoked_at = now() "
                "WHERE device_id = %s AND status = 'active'",
                (device_id,),
            )

            # 3. Record binding history
            conn.execute(
                "INSERT INTO veetee_device_binding_history "
                "(id, device_id, user_id, action, actor_user_id, metadata) "
                "VALUES (%s, %s, %s, 'unbound', %s, %s)",
                (uuid.uuid4(), device_id, cur_owner, actor_user_id, Jsonb({})),
            )

            # 4. Record audit event
            conn.execute(
                "INSERT INTO veetee_audit_events "
                "(id, actor_user_id, action, resource_type, resource_id, metadata) "
                "VALUES (%s, %s, 'device.unbind', 'device', %s, %s)",
                (
                    uuid.uuid4(),
                    actor_user_id,
                    device_id,
                    Jsonb({"previous_owner": str(cur_owner) if cur_owner else None}),
                ),
            )

            res = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices WHERE device_id = %s",
                (device_id,),
            ).fetchone()
            assert res is not None
            response = self._row_to_dict(res)
            self._save_operation(
                conn,
                idempotency_key,
                actor_user_id,
                "device.unbind",
                request_hash,
                response,
                device_id,
            )
            return response

    def patch_device(
        self,
        actor_user_id: uuid.UUID,
        device_id: str,
        alias: str | None = None,
        agent_id: uuid.UUID | None = None,
        auto_update: bool | None = None,
        channel: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            dev_row = conn.execute(
                "SELECT id FROM veetee_devices WHERE device_id = %s FOR UPDATE",
                (device_id,),
            ).fetchone()
            if not dev_row:
                return None

            updates: list[str] = []
            params: list[Any] = []
            if alias is not None:
                updates.append("alias = %s")
                params.append(alias)
            if agent_id is not None:
                updates.append("agent_id = %s")
                params.append(agent_id)
            if auto_update is not None:
                updates.append("auto_update = %s")
                params.append(auto_update)
            if channel is not None:
                updates.append("channel = %s")
                params.append(channel)

            if updates:
                updates.append("updated_at = now()")
                updates.append("version = version + 1")
                params.append(device_id)
                conn.execute(
                    f"UPDATE veetee_devices SET {', '.join(updates)} WHERE device_id = %s",
                    params,
                )
                conn.execute(
                    "INSERT INTO veetee_audit_events "
                    "(id, actor_user_id, action, resource_type, resource_id, metadata) "
                    "VALUES (%s, %s, 'device.update', 'device', %s, %s)",
                    (
                        uuid.uuid4(),
                        actor_user_id,
                        device_id,
                        Jsonb({"fields": [item.split(" =", 1)[0] for item in updates[:-2]]}),
                    ),
                )

            res = conn.execute(
                "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
                "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
                "last_seen_at, created_at, updated_at, version "
                "FROM veetee_devices WHERE device_id = %s",
                (device_id,),
            ).fetchone()
            assert res is not None
            return self._row_to_dict(res)

    def agent_belongs_to_owner(self, agent_id: uuid.UUID, owner_user_id: uuid.UUID) -> bool:
        with self.database.connection() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                    (agent_id, owner_user_id),
                ).fetchone()
                is not None
            )

    def was_unbound_by(self, device_id: str, actor_user_id: uuid.UUID) -> bool:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT actor_user_id FROM veetee_device_binding_history "
                "WHERE device_id = %s AND action = 'unbound' ORDER BY created_at DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            return row is not None and row[0] == actor_user_id

    @staticmethod
    def _operation_hash(payload: dict[str, Any]) -> str:
        import json

        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _get_operation(
        conn: Any,
        key: str,
        actor: uuid.UUID,
        action: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        if not key:
            raise ValueError("Idempotency-Key is required")
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{actor}:{action}:{key}",),
        )
        row = conn.execute(
            "SELECT request_hash, response FROM veetee_idempotency_operations "
            "WHERE operation_key = %s AND actor_user_id = %s AND action = %s FOR UPDATE",
            (key, actor, action),
        ).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise BindingConflictError("Idempotency-Key was reused with a different request")
        return cast(dict[str, Any], row[1])

    @staticmethod
    def _save_operation(
        conn: Any,
        key: str,
        actor: uuid.UUID,
        action: str,
        request_hash: str,
        response: dict[str, Any],
        resource_id: str,
    ) -> None:
        conn.execute(
            "INSERT INTO veetee_idempotency_operations "
            "(id, operation_key, actor_user_id, action, request_hash, response, resource_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4(),
                key,
                actor,
                action,
                request_hash,
                Jsonb(
                    {
                        name: (
                            value.isoformat()
                            if isinstance(value, datetime)
                            else str(value)
                            if isinstance(value, uuid.UUID)
                            else value
                        )
                        for name, value in response.items()
                    }
                ),
                resource_id,
            ),
        )

    @staticmethod
    def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "owner_user_id": row[1],
            "agent_id": row[2],
            "device_id": row[3],
            "client_id": row[4],
            "alias": row[5],
            "status": row[6],
            "board": row[7],
            "chip": row[8],
            "partition": row[9],
            "current_firmware_version": row[10],
            "auto_update": row[11],
            "channel": row[12],
            "cohort": row[13],
            "last_seen_at": row[14],
            "created_at": row[15],
            "updated_at": row[16],
            "version": row[17],
        }

    @staticmethod
    def _select_device(conn: Any, device_id: str) -> tuple[Any, ...]:
        row = conn.execute(
            "SELECT id, owner_user_id, agent_id, device_id, client_id, alias, status, "
            "board, chip, partition, current_firmware_version, auto_update, channel, cohort, "
            "last_seen_at, created_at, updated_at, version "
            "FROM veetee_devices WHERE device_id = %s",
            (device_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Device not found")
        return cast(tuple[Any, ...], row)


class ActivationRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get_or_create_challenge(
        self,
        device_id: str,
        secret: str,
        ttl_seconds: int = 600,
        max_attempts: int = 3,
    ) -> tuple[str, dict[str, Any]]:
        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (device_id,))
            live = conn.execute(
                "SELECT id, salt, expires_at, attempts, max_attempts, created_at "
                "FROM veetee_device_activation_challenges "
                "WHERE device_id = %s AND consumed_at IS NULL AND expires_at > now() "
                "ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
                (device_id,),
            ).fetchone()
            if live is not None:
                expires_at = cast(datetime, live[2])
                return derive_activation_code(secret, cast(str, live[1])), {
                    "id": live[0],
                    "device_id": device_id,
                    "expires_at": expires_at,
                    "attempts": live[3],
                    "max_attempts": live[4],
                    "created_at": live[5],
                    "timeout_ms": max(
                        0, int((expires_at - datetime.now(UTC)).total_seconds() * 1000)
                    ),
                    "is_new": False,
                }

            conn.execute(
                "UPDATE veetee_device_activation_challenges SET consumed_at = now() "
                "WHERE device_id = %s AND consumed_at IS NULL",
                (device_id,),
            )

            salt = os.urandom(8).hex()
            code = derive_activation_code(secret, salt)
            code_hash = hash_activation_code(code, secret, salt)
            ch_id = uuid.uuid4()
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

            conn.execute(
                "INSERT INTO veetee_device_activation_challenges "
                "(id, device_id, code_hash, salt, expires_at, max_attempts) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (ch_id, device_id, code_hash, salt, expires_at, max_attempts),
            )
            ch_dict = {
                "id": ch_id,
                "device_id": device_id,
                "expires_at": expires_at,
                "max_attempts": max_attempts,
                "created_at": datetime.now(UTC),
                "timeout_ms": ttl_seconds * 1000,
                "is_new": True,
            }
            return code, ch_dict

    def get_or_create_nonce(
        self,
        device_id: str,
        client_id: str,
        secret: str,
        ttl_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any] | None:
        """Returns an opaque nonce only for a provisioned device identity."""
        with self.database.connection() as conn:
            enrollment = conn.execute(
                "SELECT client_id FROM veetee_device_enrollments "
                "WHERE device_id = %s AND revoked_at IS NULL FOR UPDATE",
                (device_id,),
            ).fetchone()
            if enrollment is None or (enrollment[0] is not None and enrollment[0] != client_id):
                return None
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (device_id,))
            live = conn.execute(
                "SELECT id, expires_at, proof_verified_at FROM veetee_device_activation_challenges "
                "WHERE device_id = %s AND consumed_at IS NULL AND expires_at > now() "
                "ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
                (device_id,),
            ).fetchone()
            if live is not None:
                return {
                    "nonce": str(live[0]) if live[2] is None else "",
                    "timeout_ms": max(
                        0, int((cast(datetime, live[1]) - datetime.now(UTC)).total_seconds() * 1000)
                    ),
                }
            conn.execute(
                "UPDATE veetee_device_activation_challenges SET consumed_at = now() "
                "WHERE device_id = %s AND consumed_at IS NULL",
                (device_id,),
            )
            nonce = uuid.uuid4()
            salt = os.urandom(8).hex()
            code = derive_activation_code(secret, salt)
            conn.execute(
                "INSERT INTO veetee_device_activation_challenges "
                "(id, device_id, code_hash, salt, expires_at, max_attempts) "
                "VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s), %s)",
                (
                    nonce,
                    device_id,
                    hash_activation_code(code, secret, salt),
                    salt,
                    ttl_seconds,
                    max_attempts,
                ),
            )
            return {"nonce": str(nonce), "timeout_ms": ttl_seconds * 1000}

    def verify_enrollment_proof(
        self,
        device_id: str,
        client_id: str,
        nonce: str,
        proof: str,
        secret: str,
    ) -> tuple[str, dict[str, Any]]:
        """Consumes one nonce after Ed25519 verification and returns its physical code once."""
        try:
            nonce_id = uuid.UUID(nonce)
        except ValueError as exc:
            raise InvalidCodeError("Invalid activation nonce") from exc
        with self.database.connection() as conn:
            challenge = conn.execute(
                "SELECT c.salt, c.expires_at, c.attempts, c.max_attempts, c.proof_verified_at, "
                "e.client_id, e.ed25519_public_key "
                "FROM veetee_device_activation_challenges c "
                "JOIN veetee_device_enrollments e ON e.device_id = c.device_id "
                "WHERE c.id = %s AND c.device_id = %s AND c.consumed_at IS NULL "
                "AND e.revoked_at IS NULL FOR UPDATE",
                (nonce_id, device_id),
            ).fetchone()
            if challenge is None or challenge[4] is not None:
                raise InvalidCodeError("Activation nonce is invalid or already used")
            if cast(datetime, challenge[1]) <= datetime.now(UTC):
                raise ExpiredCodeError("Activation nonce has expired")
            if challenge[5] is not None and challenge[5] != client_id:
                raise InvalidCodeError("Activation identity does not match enrollment")
            if cast(int, challenge[2]) >= cast(int, challenge[3]):
                raise MaxAttemptsExceededError("Maximum activation proof attempts exceeded")
            message = canonical_activation_challenge(device_id, client_id, nonce)
            if not verify_ed25519_proof(cast(str, challenge[6]), proof, message):
                conn.execute(
                    "UPDATE veetee_device_activation_challenges SET attempts = attempts + 1 "
                    "WHERE id = %s",
                    (nonce_id,),
                )
                conn.commit()
                if cast(int, challenge[2]) + 1 >= cast(int, challenge[3]):
                    raise MaxAttemptsExceededError("Maximum activation proof attempts reached")
                raise InvalidCodeError("Invalid activation proof")
            conn.execute(
                "UPDATE veetee_device_activation_challenges SET proof_verified_at = now() "
                "WHERE id = %s",
                (nonce_id,),
            )
            code = derive_activation_code(secret, cast(str, challenge[0]))
            expires_at = cast(datetime, challenge[1])
            return code, {
                "nonce": nonce,
                "timeout_ms": max(0, int((expires_at - datetime.now(UTC)).total_seconds() * 1000)),
            }


class DeviceCredentialRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def issue_credential(
        self,
        device_id: str,
        client_id: str,
        ttl_seconds: int,
        secret: str,
    ) -> str:
        jti = uuid.uuid4()
        token = create_device_ws_token(
            device_id=device_id,
            client_id=client_id,
            jti=jti,
            ttl_seconds=ttl_seconds,
            secret=secret,
        )
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

        with self.database.connection() as conn:
            conn.execute(
                "UPDATE veetee_device_credentials SET status = 'revoked', revoked_at = now() "
                "WHERE device_id = %s AND client_id = %s AND status = 'active'",
                (device_id, client_id),
            )
            conn.execute(
                "INSERT INTO veetee_device_credentials "
                "(id, device_id, client_id, jti, token_hash, kind, status, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, 'ws', 'active', %s)",
                (uuid.uuid4(), device_id, client_id, jti, token_hash, expires_at),
            )
        return token

    def ensure_bootstrap_credential(
        self,
        device_id: str,
        client_id: str,
        challenge_id: uuid.UUID,
        created_at: datetime,
        expires_at: datetime,
        secret: str,
    ) -> str:
        """Creates the challenge-scoped bootstrap credential once."""
        iat = int(created_at.timestamp())
        ttl = max(1, int(expires_at.timestamp()) - iat)
        token = create_device_bootstrap_token(
            device_id, client_id, challenge_id, ttl, secret, iat=iat
        )
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO veetee_device_credentials "
                "(id, device_id, client_id, jti, token_hash, kind, status, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, 'bootstrap', 'active', %s) "
                "ON CONFLICT (jti) DO NOTHING",
                (
                    uuid.uuid4(),
                    device_id,
                    client_id,
                    challenge_id,
                    token_hash,
                    expires_at,
                ),
            )
        return token

    @staticmethod
    def _insert_bootstrap_credential(
        conn: Any,
        device_id: str,
        client_id: str,
        ttl_seconds: int,
        secret: str,
        kind: str,
    ) -> str:
        jti = uuid.uuid4()
        token = create_device_bootstrap_token(device_id, client_id, jti, ttl_seconds, secret)
        conn.execute(
            "UPDATE veetee_device_credentials SET status = 'revoked', revoked_at = now() "
            "WHERE device_id = %s AND status = 'active'",
            (device_id,),
        )
        conn.execute(
            "INSERT INTO veetee_device_credentials "
            "(id, device_id, client_id, jti, token_hash, kind, status, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'active', now() + make_interval(secs => %s))",
            (
                uuid.uuid4(),
                device_id,
                client_id,
                jti,
                hashlib.sha256(token.encode()).hexdigest(),
                kind,
                ttl_seconds,
            ),
        )
        return token

    def verify_credential(
        self,
        device_id: str,
        client_id: str,
        token: str,
        secret: str,
    ) -> bool:
        payload = verify_device_ws_token(token, secret)
        if not payload:
            return False
        if payload.get("device_id") != device_id or payload.get("client_id") != client_id:
            return False
        jti_str = payload.get("jti")
        if not jti_str:
            return False
        try:
            jti = uuid.UUID(jti_str)
        except ValueError:
            return False

        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT status FROM veetee_device_credentials "
                "WHERE jti = %s AND device_id = %s AND client_id = %s "
                "AND kind = 'ws' AND expires_at > now()",
                (jti, device_id, client_id),
            ).fetchone()
            if not row:
                return False
            return str(row[0]) == "active"


class OtaRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def create_artifact(
        self,
        artifact_id: uuid.UUID,
        board: str,
        chip: str,
        partition: str,
        file_name: str,
        file_path: str,
        file_size: int,
        sha256: str,
        signature: str = "",
        signature_algorithm: str = "ed25519",
        signature_key_id: str = "primary",
        provenance: str = "",
        metadata: dict[str, Any] | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        provenance = provenance.strip()
        if not 1 <= len(provenance) <= 512:
            raise ValueError("Artifact provenance must contain 1 to 512 characters")
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO veetee_ota_artifacts "
                "(id, board, chip, partition, file_name, file_path, file_size, sha256, signature, "
                "signature_algorithm, signature_key_id, provenance, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    artifact_id,
                    board,
                    chip,
                    partition,
                    file_name,
                    file_path,
                    file_size,
                    sha256,
                    signature,
                    signature_algorithm,
                    signature_key_id,
                    provenance,
                    Jsonb(metadata or {}),
                ),
            )
            if actor_user_id is not None:
                self._audit(
                    conn,
                    actor_user_id,
                    "ota.artifact_upload",
                    "ota_artifact",
                    str(artifact_id),
                )
            row = conn.execute(
                "SELECT id, board, chip, partition, file_name, file_path, file_size, sha256, "
                "signature, signature_algorithm, signature_key_id, provenance, metadata, "
                "created_at "
                "FROM veetee_ota_artifacts WHERE id = %s",
                (artifact_id,),
            ).fetchone()
            assert row is not None
            return self._artifact_row_to_dict(row)

    def get_artifact(self, artifact_id: uuid.UUID) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT id, board, chip, partition, file_name, file_path, file_size, sha256, "
                "signature, signature_algorithm, signature_key_id, provenance, metadata, "
                "created_at "
                "FROM veetee_ota_artifacts WHERE id = %s",
                (artifact_id,),
            ).fetchone()
            return self._artifact_row_to_dict(row) if row else None

    def get_artifact_for_device(
        self, artifact_id: uuid.UUID, device_id: str
    ) -> dict[str, Any] | None:
        device = DeviceRepository(self.database).get_by_device_id(device_id)
        if device is None or device["status"] != "bound" or not device["auto_update"]:
            return None
        eligible = self.get_eligible_release(
            device_id,
            device["board"],
            device["chip"],
            device["partition"],
            device["current_firmware_version"],
            device["channel"],
        )
        if eligible is None or eligible[1]["id"] != artifact_id:
            return None
        return eligible[1]

    def list_artifacts(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT id, board, chip, partition, file_name, file_path, file_size, sha256, "
                "signature, signature_algorithm, signature_key_id, provenance, metadata, "
                "created_at "
                "FROM veetee_ota_artifacts ORDER BY created_at DESC",
            ).fetchall()
            return [self._artifact_row_to_dict(r) for r in rows]

    def create_release(
        self,
        version: str,
        artifact_id: uuid.UUID,
        board: str,
        chip: str,
        partition: str,
        channel: str = "stable",
        min_current_version: str = "",
        provenance: str = "",
        rollback_target_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        provenance = provenance.strip()
        if not 1 <= len(provenance) <= 512:
            raise ValueError("Release provenance must contain 1 to 512 characters")
        release_id = uuid.uuid4()
        with self.database.connection() as conn:
            if rollback_target_id is not None:
                target = conn.execute(
                    "SELECT version, board, chip, partition, channel, is_published, artifact_id "
                    "FROM veetee_ota_releases WHERE id = %s",
                    (rollback_target_id,),
                ).fetchone()
                if target is None or not target[5]:
                    raise ValueError("Rollback target must be a published release")
                if tuple(target[1:5]) != (board, chip, partition, channel):
                    raise ValueError("Rollback target must match release target and channel")
                if compare_semver(cast(str, target[0]), version) >= 0:
                    raise ValueError("Rollback target version must be lower than release version")
                artifact = conn.execute(
                    "SELECT file_size, sha256, signature, provenance FROM veetee_ota_artifacts "
                    "WHERE id = %s",
                    (target[6],),
                ).fetchone()
                if (
                    artifact is None
                    or cast(int, artifact[0]) <= 0
                    or len(cast(str, artifact[1])) != 64
                    or len(cast(str, artifact[2])) != 128
                    or not cast(str, artifact[3]).strip()
                ):
                    raise ValueError("Rollback target artifact is invalid")
            conn.execute(
                "INSERT INTO veetee_ota_releases "
                "(id, version, artifact_id, board, chip, partition, channel, min_current_version, "
                "provenance, rollback_target_id, is_published, published_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, NULL)",
                (
                    release_id,
                    version,
                    artifact_id,
                    board,
                    chip,
                    partition,
                    channel,
                    min_current_version,
                    provenance,
                    rollback_target_id,
                ),
            )
            if actor_user_id is not None:
                self._audit(
                    conn,
                    actor_user_id,
                    "ota.release_create",
                    "ota_release",
                    str(release_id),
                )
            row = conn.execute(
                "SELECT id, version, artifact_id, board, chip, partition, channel, "
                "min_current_version, is_published, published_at, created_at "
                "FROM veetee_ota_releases WHERE id = %s",
                (release_id,),
            ).fetchone()
            assert row is not None
            result = self._release_row_to_dict(row)
            result["provenance"] = provenance
            result["rollback_target_id"] = rollback_target_id
            return result

    def publish_release(
        self,
        release_id: uuid.UUID,
        channel: str = "stable",
        cohort_percentage: int = 100,
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            release = conn.execute(
                "SELECT id, version, artifact_id, board, chip, partition, channel, "
                "min_current_version, is_published, published_at, created_at "
                "FROM veetee_ota_releases WHERE id = %s FOR UPDATE",
                (release_id,),
            ).fetchone()
            if release is None:
                return None
            if not cast(bool, release[8]):
                conn.execute(
                    "UPDATE veetee_ota_releases SET is_published = true, published_at = now() "
                    "WHERE id = %s",
                    (release_id,),
                )
            # Create or activate rollout
            r_row = conn.execute(
                "SELECT id FROM veetee_ota_rollouts "
                "WHERE release_id = %s AND kind = 'release'",
                (release_id,),
            ).fetchone()
            if r_row:
                conn.execute(
                    "UPDATE veetee_ota_rollouts SET status = 'active', "
                    "cohort_percentage = %s, updated_at = now() "
                    "WHERE id = %s",
                    (cohort_percentage, r_row[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO veetee_ota_rollouts "
                    "(id, release_id, channel, cohort_percentage, status) "
                    "VALUES (%s, %s, %s, %s, 'active')",
                    (uuid.uuid4(), release_id, channel, cohort_percentage),
                )
            if actor_user_id is not None:
                self._audit(
                    conn,
                    actor_user_id,
                    "ota.release_publish",
                    "ota_release",
                    str(release_id),
                )
            row = conn.execute(
                "SELECT id, version, artifact_id, board, chip, partition, channel, "
                "min_current_version, is_published, published_at, created_at "
                "FROM veetee_ota_releases WHERE id = %s",
                (release_id,),
            ).fetchone()
            return self._release_row_to_dict(row) if row else None

    def update_rollout_status(
        self,
        rollout_id: uuid.UUID,
        status: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE veetee_ota_rollouts SET status = %s, updated_at = now() WHERE id = %s",
                (status, rollout_id),
            )
            if actor_user_id is not None:
                self._audit(
                    conn,
                    actor_user_id,
                    f"ota.rollout_{status}",
                    "ota_rollout",
                    str(rollout_id),
                )
            row = conn.execute(
                "SELECT id, release_id, channel, cohort_percentage, status, created_at, "
                "updated_at, "
                "kind, rollback_scope, rollback_device_id, rollback_cohort "
                "FROM veetee_ota_rollouts WHERE id = %s",
                (rollout_id,),
            ).fetchone()
            return self._rollout_row_to_dict(row) if row else None

    def activate_rollback_target(
        self,
        rollout_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        scope: str = "rollout",
        device_id: str | None = None,
        cohort: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            source = conn.execute(
                "SELECT ro.id, r.rollback_target_id FROM veetee_ota_rollouts ro "
                "JOIN veetee_ota_releases r ON r.id = ro.release_id "
                "WHERE ro.id = %s FOR UPDATE",
                (rollout_id,),
            ).fetchone()
            if source is None:
                return None
            if source[1] is None:
                raise ValueError("Release has no rollback target")
            target = conn.execute(
                "SELECT is_published FROM veetee_ota_releases WHERE id = %s",
                (source[1],),
            ).fetchone()
            if target is None or not target[0]:
                raise ValueError("Rollback target is not published")
            if scope == "rollout":
                conn.execute(
                    "UPDATE veetee_ota_rollouts SET status = 'killed', updated_at = now() "
                    "WHERE id = %s",
                    (rollout_id,),
                )
            conn.execute(
                "UPDATE veetee_ota_rollouts SET status = 'paused', updated_at = now() "
                "WHERE release_id = %s AND kind = 'release' AND status = 'active'",
                (source[1],),
            )
            target_rollout_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO veetee_ota_rollouts "
                "(id, release_id, channel, cohort_percentage, status, kind, rollback_scope, "
                "rollback_device_id, rollback_cohort) "
                "SELECT %s, id, channel, 100, 'active', 'rollback', %s, %s, %s "
                "FROM veetee_ota_releases WHERE id = %s",
                (target_rollout_id, scope, device_id, cohort, source[1]),
            )
            conn.execute(
                "INSERT INTO veetee_ota_rollback_authorizations "
                "(id, source_rollout_id, source_release_id, target_release_id, "
                "target_rollout_id, scope, device_id, cohort, created_by) "
                "SELECT %s, ro.id, ro.release_id, %s, %s, %s, %s, %s, %s "
                "FROM veetee_ota_rollouts ro WHERE ro.id = %s",
                (
                    uuid.uuid4(),
                    source[1],
                    target_rollout_id,
                    scope,
                    device_id,
                    cohort,
                    actor_user_id,
                    rollout_id,
                ),
            )
            self._audit(
                conn, actor_user_id, "ota.rollback_activate", "ota_rollout", str(rollout_id)
            )
            row = conn.execute(
                "SELECT id, release_id, channel, cohort_percentage, status, created_at, "
                "updated_at, "
                "kind, rollback_scope, rollback_device_id, rollback_cohort "
                "FROM veetee_ota_rollouts WHERE id = %s",
                (target_rollout_id,),
            ).fetchone()
            return self._rollout_row_to_dict(row) if row else None

    def list_releases(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT id, version, artifact_id, board, chip, partition, channel, "
                "min_current_version, is_published, published_at, created_at, "
                "provenance, rollback_target_id "
                "FROM veetee_ota_releases ORDER BY created_at DESC",
            ).fetchall()
            results = []
            for row in rows:
                release = self._release_row_to_dict(row[:11])
                release["provenance"] = row[11]
                release["rollback_target_id"] = row[12]
                results.append(release)
            return results

    def list_rollouts(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT id, release_id, channel, cohort_percentage, status, created_at, "
                "updated_at, "
                "kind, rollback_scope, rollback_device_id, rollback_cohort "
                "FROM veetee_ota_rollouts ORDER BY created_at DESC",
            ).fetchall()
            return [self._rollout_row_to_dict(r) for r in rows]

    def get_eligible_release(
        self,
        device_id: str,
        board: str,
        chip: str,
        partition: str,
        current_version: str,
        channel: str = "stable",
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Finds the highest eligible active release for a device."""
        try:
            parse_semver(current_version)
        except ValueError:
            return None
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT r.id, r.version, r.artifact_id, r.board, r.chip, r.partition, "
                "r.channel, r.min_current_version, r.is_published, r.published_at, "
                "r.created_at, "
                "ro.id, ro.cohort_percentage, ro.status, ro.kind, "
                "EXISTS (SELECT 1 FROM veetee_ota_rollback_authorizations ra "
                "WHERE ra.target_release_id = r.id AND ra.target_rollout_id = ro.id "
                "AND ra.revoked_at IS NULL AND (ra.scope = 'rollout' "
                "OR (ra.scope = 'device' AND ra.device_id = %s) "
                "OR (ra.scope = 'cohort' AND ra.cohort = "
                "(SELECT cohort FROM veetee_devices WHERE device_id = %s)))) AS rollback_allowed "
                "FROM veetee_ota_releases r "
                "JOIN veetee_ota_rollouts ro ON ro.release_id = r.id "
                "WHERE r.is_published = true AND ro.status = 'active' "
                "AND r.channel = %s "
                "AND r.board = %s AND r.chip = %s AND r.partition = %s "
                "ORDER BY r.created_at DESC",
                (device_id, device_id, channel, board, chip, partition),
            ).fetchall()

            candidates: list[dict[str, Any]] = []
            for row in rows:
                rel = self._release_row_to_dict(row[:11])
                rollout_id = cast(uuid.UUID, row[11])
                cohort_percentage = cast(int, row[12])
                rollout_kind = cast(str, row[14])
                rollback_allowed = cast(bool, row[15])
                target_version = rel["version"]
                min_curr = rel["min_current_version"]

                # Scoped rollback rollouts are invisible without a matching
                # authorization, regardless of the current version direction.
                if rollout_kind == "rollback" and not rollback_allowed:
                    continue

                # Check anti-rollback & version progression
                version_order = compare_semver(target_version, current_version)
                if version_order == 0 or (version_order < 0 and not rollback_allowed):
                    continue
                if min_curr and compare_semver(current_version, min_curr) < 0:
                    continue

                # Check cohort percentage
                if not is_device_in_cohort(device_id, rollout_id, cohort_percentage):
                    continue

                # Fetch artifact
                candidates.append(rel)
            candidates.sort(
                key=cmp_to_key(
                    lambda left, right: compare_semver(left["version"], right["version"])
                ),
                reverse=True,
            )
            for rel in candidates:
                art = self.get_artifact(rel["artifact_id"])
                if art:
                    with self.database.connection() as offer_conn:
                        offer_conn.execute(
                            "INSERT INTO veetee_ota_offers (id, device_id, release_id) "
                            "VALUES (%s, %s, %s) ON CONFLICT (device_id, release_id) "
                            "DO UPDATE SET offered_at = now()",
                            (uuid.uuid4(), device_id, rel["id"]),
                        )
                    return rel, art
            return None

    def record_report(
        self,
        event_id: uuid.UUID,
        device_id: str,
        release_id: uuid.UUID | None,
        version: str,
        stage: str,
        outcome: str,
        error_message: str = "",
        metadata: dict[str, Any] | None = None,
        failure_minimum: int = 3,
        sample_threshold: int = 10,
        failure_percentage: int = 25,
        max_reports_per_hour: int = 120,
        dedupe_window_seconds: int = 30,
    ) -> dict[str, Any]:
        """Idempotent recording of device OTA events."""
        with self.database.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"ota-report:{device_id}",),
            )
            # Check existing event_id
            existing = conn.execute(
                "SELECT id, event_id, device_id, release_id, version, stage, outcome, "
                "error_message, metadata, created_at "
                "FROM veetee_ota_reports WHERE event_id = %s",
                (event_id,),
            ).fetchone()
            if existing:
                stored = self._report_row_to_dict(existing)
                incoming = {
                    "device_id": device_id,
                    "release_id": release_id,
                    "version": version,
                    "stage": stage,
                    "outcome": outcome,
                    "error_message": error_message,
                    "metadata": metadata or {},
                }
                if any(stored[key] != value for key, value in incoming.items()):
                    raise ValueError("event_id was already used with different report data")
                return stored

            quota = conn.execute(
                "SELECT count(*) FROM veetee_ota_reports WHERE device_id = %s "
                "AND created_at >= now() - interval '1 hour'",
                (device_id,),
            ).fetchone()
            assert quota is not None
            if cast(int, quota[0]) >= max_reports_per_hour:
                raise ReportRateLimitError("OTA report quota exceeded for this device")

            if outcome == "in_progress" or stage == "check":
                duplicate = conn.execute(
                    "SELECT id, event_id, device_id, release_id, version, stage, outcome, "
                    "error_message, metadata, created_at FROM veetee_ota_reports "
                    "WHERE device_id = %s AND release_id IS NOT DISTINCT FROM %s "
                    "AND stage = %s AND outcome = %s "
                    "AND created_at >= now() - make_interval(secs => %s) "
                    "ORDER BY created_at DESC LIMIT 1",
                    (device_id, release_id, stage, outcome, dedupe_window_seconds),
                ).fetchone()
                if duplicate is not None:
                    return self._report_row_to_dict(duplicate)

            device = conn.execute(
                "SELECT current_firmware_version, board, chip, partition FROM veetee_devices "
                "WHERE device_id = %s AND status = 'bound' FOR UPDATE",
                (device_id,),
            ).fetchone()
            if device is None:
                raise ValueError("Device is not bound")
            release = None
            if stage in {"download", "install", "boot", "rollback"} and release_id is None:
                raise ValueError("release_id is required for this OTA stage")
            if release_id is not None:
                release = conn.execute(
                    "SELECT version, board, chip, partition, is_published "
                    "FROM veetee_ota_releases WHERE id = %s",
                    (release_id,),
                ).fetchone()
                if release is None or release[0] != version or not release[4]:
                    raise ValueError("Report must match a published release")
                if tuple(release[1:4]) != tuple(device[1:4]):
                    raise ValueError("Report release target does not match device")
                offered = conn.execute(
                    "SELECT 1 FROM veetee_ota_offers WHERE device_id = %s AND release_id = %s",
                    (device_id, release_id),
                ).fetchone()
                if offered is None:
                    raise ValueError("Release was not offered to this device")
                rollout = conn.execute(
                    "SELECT id FROM veetee_ota_rollouts WHERE release_id = %s FOR UPDATE",
                    (release_id,),
                ).fetchone()
                if rollout is None:
                    raise ValueError("Release rollout does not exist")
                successful = {
                    row[0]
                    for row in conn.execute(
                        "SELECT stage FROM veetee_ota_reports "
                        "WHERE device_id = %s AND release_id = %s AND outcome = 'success'",
                        (device_id, release_id),
                    ).fetchall()
                }
                required_previous = {
                    "download": set(),
                    "install": {"download"},
                    "boot": {"download", "install"},
                    "rollback": {"download", "install"},
                }.get(stage, set())
                if not required_previous.issubset(successful):
                    raise ValueError("OTA report stage is out of order")
                if outcome in {"success", "failure", "skipped"}:
                    duplicate_terminal = conn.execute(
                        "SELECT 1 FROM veetee_ota_reports "
                        "WHERE device_id = %s AND release_id = %s AND stage = %s "
                        "AND outcome IN ('success', 'failure', 'skipped')",
                        (device_id, release_id, stage),
                    ).fetchone()
                    if duplicate_terminal is not None:
                        raise ValueError("Terminal OTA stage was already reported")

            rep_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO veetee_ota_reports "
                "(id, event_id, device_id, release_id, version, stage, outcome, "
                "error_message, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    rep_id,
                    event_id,
                    device_id,
                    release_id,
                    version,
                    stage,
                    outcome,
                    error_message,
                    Jsonb(metadata or {}),
                ),
            )

            # Update current_firmware_version on boot success
            if stage == "boot" and outcome == "success" and release is not None:
                try:
                    if compare_semver(version, cast(str, device[0])) < 0:
                        authorized = conn.execute(
                            "SELECT 1 FROM veetee_ota_rollback_authorizations ra "
                            "WHERE ra.target_release_id = %s AND ra.revoked_at IS NULL "
                            "AND (ra.scope = 'rollout' "
                            "OR (ra.scope = 'device' AND ra.device_id = %s) "
                            "OR (ra.scope = 'cohort' AND ra.cohort = "
                            "(SELECT cohort FROM veetee_devices WHERE device_id = %s)))",
                            (release_id, device_id, device_id),
                        ).fetchone()
                        if authorized is None:
                            raise ValueError("Boot report would roll device version backward")
                except ValueError as exc:
                    raise ValueError("Invalid or rollback firmware version") from exc
                conn.execute(
                    "UPDATE veetee_devices SET current_firmware_version = %s, "
                    "updated_at = now() WHERE device_id = %s",
                    (version, device_id),
                )

            if release_id is not None and outcome == "failure":
                sample = conn.execute(
                    "WITH latest AS ("
                    " SELECT DISTINCT ON (device_id, stage, release_id) outcome "
                    " FROM veetee_ota_reports WHERE release_id = %s "
                    " AND stage IN ('install', 'boot') "
                    " AND outcome IN ('success', 'failure', 'skipped') "
                    " ORDER BY device_id, stage, release_id, created_at DESC"
                    ") SELECT count(*), count(*) FILTER (WHERE outcome = 'failure') FROM latest",
                    (release_id,),
                ).fetchone()
                assert sample is not None
                total, failures = cast(int, sample[0]), cast(int, sample[1])
                if (
                    total >= sample_threshold
                    and failures >= failure_minimum
                    and failures * 100 >= total * failure_percentage
                ):
                    conn.execute(
                        "UPDATE veetee_ota_rollouts SET status = 'paused', updated_at = now() "
                        "WHERE release_id = %s AND status = 'active'",
                        (release_id,),
                    )

            row = conn.execute(
                "SELECT id, event_id, device_id, release_id, version, stage, outcome, "
                "error_message, metadata, created_at "
                "FROM veetee_ota_reports WHERE id = %s",
                (rep_id,),
            ).fetchone()
            assert row is not None
            return self._report_row_to_dict(row)

    def cleanup_reports(self, retention_days: int) -> int:
        """Explicit operator-invoked retention cleanup; never called automatically."""
        with self.database.connection() as conn:
            conn.execute("SET LOCAL veetee.allow_report_cleanup = 'on'")
            result = conn.execute(
                "DELETE FROM veetee_ota_reports "
                "WHERE created_at < now() - make_interval(days => %s)",
                (retention_days,),
            )
            return result.rowcount or 0

    def get_summary_counts(self) -> dict[str, Any]:
        with self.database.connection() as conn:
            devices_row = conn.execute("SELECT count(*) FROM veetee_devices").fetchone()
            bound_row = conn.execute(
                "SELECT count(*) FROM veetee_devices WHERE status = 'bound'"
            ).fetchone()
            releases_row = conn.execute("SELECT count(*) FROM veetee_ota_releases").fetchone()
            active_row = conn.execute(
                "SELECT count(*) FROM veetee_ota_rollouts WHERE status = 'active'"
            ).fetchone()
            reports_row = conn.execute("SELECT count(*) FROM veetee_ota_reports").fetchone()
            assert devices_row is not None
            assert bound_row is not None
            assert releases_row is not None
            assert active_row is not None
            assert reports_row is not None
            groups = conn.execute(
                "SELECT d.board, d.current_firmware_version, d.cohort, count(*) "
                "FROM veetee_devices d GROUP BY d.board, d.current_firmware_version, d.cohort "
                "ORDER BY d.board, d.current_firmware_version, d.cohort"
            ).fetchall()
            return {
                "total_devices": cast(int, devices_row[0]),
                "bound_devices": cast(int, bound_row[0]),
                "total_releases": cast(int, releases_row[0]),
                "active_rollouts": cast(int, active_row[0]),
                "total_reports": cast(int, reports_row[0]),
                "devices_by_board_version_cohort": [
                    {
                        "board": row[0],
                        "version": row[1],
                        "cohort": row[2],
                        "count": row[3],
                    }
                    for row in groups
                ],
            }

    @staticmethod
    def _artifact_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "board": row[1],
            "chip": row[2],
            "partition": row[3],
            "file_name": row[4],
            "file_path": row[5],
            "file_size": row[6],
            "sha256": row[7],
            "signature": row[8],
            "signature_algorithm": row[9],
            "signature_key_id": row[10],
            "provenance": row[11],
            "metadata": row[12],
            "created_at": row[13],
        }

    @staticmethod
    def _audit(
        conn: Any,
        actor_user_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        conn.execute(
            "INSERT INTO veetee_audit_events "
            "(id, actor_user_id, action, resource_type, resource_id, metadata) "
            "VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)",
            (uuid.uuid4(), actor_user_id, action, resource_type, resource_id),
        )

    @staticmethod
    def _release_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "version": row[1],
            "artifact_id": row[2],
            "board": row[3],
            "chip": row[4],
            "partition": row[5],
            "channel": row[6],
            "min_current_version": row[7],
            "is_published": row[8],
            "published_at": row[9],
            "created_at": row[10],
        }

    @staticmethod
    def _rollout_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        result = {
            "id": row[0],
            "release_id": row[1],
            "channel": row[2],
            "cohort_percentage": row[3],
            "status": row[4],
            "created_at": row[5],
            "updated_at": row[6],
        }
        if len(row) > 7:
            result.update(
                {
                    "kind": row[7],
                    "rollback_scope": row[8],
                    "rollback_device_id": row[9],
                    "rollback_cohort": row[10],
                }
            )
        return result

    @staticmethod
    def _report_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "event_id": row[1],
            "device_id": row[2],
            "release_id": row[3],
            "version": row[4],
            "stage": row[5],
            "outcome": row[6],
            "error_message": row[7],
            "metadata": row[8],
            "created_at": row[9],
        }
