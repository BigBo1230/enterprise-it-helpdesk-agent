import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ai_agent_template.tickets.schemas import (
    TicketAuditEvent,
    TicketCreate,
    TicketOutboxSummary,
    TicketRecord,
)


class IdempotencyConflictError(ValueError):
    """A key was already used for a different create-ticket payload."""


class TicketStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL,
                requester TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_team TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'demo',
                external_system TEXT,
                external_key TEXT,
                integration_status TEXT NOT NULL DEFAULT 'not_configured',
                notification_status TEXT NOT NULL DEFAULT 'not_configured'
            )""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
            if "tenant_id" not in columns:
                conn.execute(
                    "ALTER TABLE tickets ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'demo'"
                )
            if "external_system" not in columns:
                conn.execute("ALTER TABLE tickets ADD COLUMN external_system TEXT")
            if "external_key" not in columns:
                conn.execute("ALTER TABLE tickets ADD COLUMN external_key TEXT")
            if "integration_status" not in columns:
                conn.execute(
                    "ALTER TABLE tickets ADD COLUMN integration_status "
                    "TEXT NOT NULL DEFAULT 'not_configured'"
                )
            if "notification_status" not in columns:
                conn.execute(
                    "ALTER TABLE tickets ADD COLUMN notification_status "
                    "TEXT NOT NULL DEFAULT 'not_configured'"
                )
            conn.execute("""CREATE TABLE IF NOT EXISTS ticket_idempotency (
                tenant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, idempotency_key)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS ticket_audit_events (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                ticket_id TEXT,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                outcome TEXT NOT NULL,
                request_id TEXT,
                idempotency_fingerprint TEXT,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS ticket_outbox (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_id TEXT,
                actor TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                lease_until TEXT,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (tenant_id, idempotency_key)
            )""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_ticket_outbox_ready "
                "ON ticket_outbox (status, next_attempt_at, created_at)"
            )
            conn.execute("""CREATE TABLE IF NOT EXISTS ticket_store_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )""")
            migration = conn.execute(
                "INSERT OR IGNORE INTO ticket_store_migrations (name, applied_at) "
                "VALUES ('outbox_success_backfill_v1', ?)",
                (datetime.now(UTC).isoformat(),),
            )
            if migration.rowcount:
                conn.execute(
                    "INSERT OR IGNORE INTO ticket_outbox (event_id, tenant_id, ticket_id, "
                    "event_type, idempotency_key, request_id, actor, status, attempt_count, "
                    "next_attempt_at, lease_until, last_error_code, created_at, updated_at) "
                    "SELECT lower(hex(randomblob(16))), tickets.tenant_id, tickets.id, "
                    "'ticket.create', 'ticket-create:' || tickets.tenant_id || ':' || tickets.id, "
                    "NULL, 'migration', 'succeeded', 1, tickets.created_at, NULL, NULL, "
                    "tickets.created_at, tickets.updated_at FROM tickets "
                    "WHERE tickets.integration_status = 'synced' "
                    "AND tickets.external_key IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM ticket_outbox AS events "
                    "WHERE events.tenant_id = tickets.tenant_id "
                    "AND events.idempotency_key = 'ticket-create:' || tickets.tenant_id || ':' "
                    "|| tickets.id)"
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(
        self,
        data: TicketCreate,
        tenant_id: str = "demo",
        idempotency_key: str | None = None,
        request_id: str | None = None,
        actor: str = "system",
        integration_system: str | None = None,
        notifications_enabled: bool = False,
    ) -> TicketRecord:
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
            raise ValueError("Idempotency-Key must be between 1 and 128 characters.")
        request_json = json.dumps(
            data.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()
        issue_text = f"{data.title} {data.description}".casefold()
        assigned_team = (
            "network-team"
            if any(keyword in issue_text for keyword in ("vpn", "网络", "network"))
            else "service-desk"
        )
        record = TicketRecord(
            **data.model_dump(),
            tenant_id=tenant_id,
            id=f"IT-{uuid4().hex[:10].upper()}",
            status="open",
            assigned_team=assigned_team,
            external_system=integration_system,
            integration_status="pending" if integration_system else "not_configured",
            notification_status="pending" if notifications_enabled else "not_configured",
            created_at=now,
            updated_at=now,
        )
        conflict_ticket_id: str | None = None
        with self._connect() as conn:
            if idempotency_key:
                conn.execute("BEGIN IMMEDIATE")
                previous = conn.execute(
                    "SELECT request_hash, response_json FROM ticket_idempotency "
                    "WHERE tenant_id = ? AND idempotency_key = ?",
                    (tenant_id, idempotency_key),
                ).fetchone()
                if previous:
                    if previous["request_hash"] != request_hash:
                        existing = TicketRecord.model_validate_json(previous["response_json"])
                        conflict_ticket_id = existing.id
                        self._insert_audit_event(
                            conn,
                            tenant_id=tenant_id,
                            ticket_id=existing.id,
                            event_type="ticket.create.idempotency_conflict",
                            actor=actor,
                            outcome="rejected",
                            request_id=request_id,
                            idempotency_key=idempotency_key,
                            details={"reason": "request_body_mismatch"},
                        )
                    else:
                        stored = TicketRecord.model_validate_json(previous["response_json"])
                        current = conn.execute(
                            "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?",
                            (stored.id, tenant_id),
                        ).fetchone()
                        existing = TicketRecord.model_validate(dict(current)) if current else stored
                        if integration_system:
                            pending = conn.execute(
                                "SELECT event_id, status FROM ticket_outbox "
                                "WHERE tenant_id = ? AND idempotency_key = ?",
                                (tenant_id, f"ticket-create:{tenant_id}:{existing.id}"),
                            ).fetchone()
                            if pending and pending["status"] == "failed":
                                now = datetime.now(UTC).isoformat()
                                conn.execute(
                                    "UPDATE ticket_outbox SET status = 'pending', "
                                    "attempt_count = 0, next_attempt_at = ?, lease_until = NULL, "
                                    "last_error_code = NULL, request_id = ?, actor = ?, "
                                    "updated_at = ? WHERE event_id = ?",
                                    (now, request_id, actor, now, pending["event_id"]),
                                )
                                conn.execute(
                                    "UPDATE tickets SET integration_status = 'pending', "
                                    "updated_at = ? WHERE id = ? AND tenant_id = ?",
                                    (now, existing.id, tenant_id),
                                )
                                current = conn.execute(
                                    "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?",
                                    (existing.id, tenant_id),
                                ).fetchone()
                                existing = TicketRecord.model_validate(dict(current))
                                self._insert_audit_event(
                                    conn,
                                    tenant_id=tenant_id,
                                    ticket_id=existing.id,
                                    event_type="integration.sync.requeued",
                                    actor=actor,
                                    outcome="queued",
                                    request_id=request_id,
                                    idempotency_key=idempotency_key,
                                    details={"reason": "same_request_retried"},
                                )
                        if notifications_enabled:
                            notification = conn.execute(
                                "SELECT event_id, status FROM ticket_outbox "
                                "WHERE tenant_id = ? AND idempotency_key = ?",
                                (tenant_id, f"slack-created:{tenant_id}:{existing.id}"),
                            ).fetchone()
                            if notification and notification["status"] == "failed":
                                now = datetime.now(UTC).isoformat()
                                conn.execute(
                                    "UPDATE ticket_outbox SET status = 'pending', "
                                    "attempt_count = 0, next_attempt_at = ?, lease_until = NULL, "
                                    "last_error_code = NULL, request_id = ?, actor = ?, "
                                    "updated_at = ? WHERE event_id = ?",
                                    (now, request_id, actor, now, notification["event_id"]),
                                )
                                self._refresh_notification_status(conn, tenant_id, existing.id, now)
                                current = conn.execute(
                                    "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?",
                                    (existing.id, tenant_id),
                                ).fetchone()
                                existing = TicketRecord.model_validate(dict(current))
                                self._insert_audit_event(
                                    conn,
                                    tenant_id=tenant_id,
                                    ticket_id=existing.id,
                                    event_type="slack.notification.requeued",
                                    actor=actor,
                                    outcome="queued",
                                    request_id=request_id,
                                    idempotency_key=idempotency_key,
                                    details={"reason": "same_request_retried"},
                                )
                        self._insert_audit_event(
                            conn,
                            tenant_id=tenant_id,
                            ticket_id=existing.id,
                            event_type="ticket.create.replayed",
                            actor=actor,
                            outcome="replayed",
                            request_id=request_id,
                            idempotency_key=idempotency_key,
                            details={"status": existing.status},
                        )
                        return existing

            if conflict_ticket_id is None:
                conn.execute(
                    "INSERT INTO tickets (id, title, description, priority, requester, status, "
                    "assigned_team, created_at, updated_at, tenant_id, external_system, "
                    "integration_status, notification_status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.title,
                        record.description,
                        record.priority,
                        record.requester,
                        record.status,
                        record.assigned_team,
                        now,
                        now,
                        tenant_id,
                        record.external_system,
                        record.integration_status,
                        record.notification_status,
                    ),
                )
                if integration_system:
                    conn.execute(
                        "INSERT INTO ticket_outbox (event_id, tenant_id, ticket_id, event_type, "
                        "idempotency_key, request_id, actor, status, attempt_count, "
                        "next_attempt_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, 'ticket.create', ?, ?, ?, 'pending', 0, ?, ?, ?)",
                        (
                            uuid4().hex,
                            tenant_id,
                            record.id,
                            f"ticket-create:{tenant_id}:{record.id}",
                            request_id,
                            actor,
                            now,
                            now,
                            now,
                        ),
                    )
                if notifications_enabled:
                    self._enqueue_slack_event(
                        conn,
                        tenant_id=tenant_id,
                        ticket_id=record.id,
                        event_type="slack.ticket_created",
                        idempotency_key=f"slack-created:{tenant_id}:{record.id}",
                        request_id=request_id,
                        actor=actor,
                        now=now,
                    )
            if idempotency_key and conflict_ticket_id is None:
                conn.execute(
                    "INSERT INTO ticket_idempotency VALUES (?, ?, ?, ?, ?)",
                    (tenant_id, idempotency_key, request_hash, record.model_dump_json(), now),
                )
            if conflict_ticket_id is None:
                self._insert_audit_event(
                    conn,
                    tenant_id=tenant_id,
                    ticket_id=record.id,
                    event_type="ticket.created",
                    actor=actor,
                    outcome="success",
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                    details={
                        "status": record.status,
                        "assigned_team": record.assigned_team,
                        "integration_status": record.integration_status,
                    },
                )
        if conflict_ticket_id is not None:
            raise IdempotencyConflictError(
                "This Idempotency-Key was already used with a different request body."
            )
        return record

    def enqueue_escalation(
        self,
        ticket_id: str,
        *,
        tenant_id: str,
        request_id: str | None = None,
        actor: str = "system",
    ) -> TicketRecord | None:
        now = datetime.now(UTC).isoformat()
        idempotency_key = f"ticket-escalate:{tenant_id}:{ticket_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?",
                (ticket_id, tenant_id),
            ).fetchone()
            if row is None:
                return None
            current = TicketRecord.model_validate(dict(row))
            if current.status == "escalated":
                self._insert_audit_event(
                    conn,
                    tenant_id=tenant_id,
                    ticket_id=ticket_id,
                    event_type="ticket.escalation.replayed",
                    actor=actor,
                    outcome="no_change",
                    request_id=request_id,
                    idempotency_key=None,
                    details={"status": "escalated", "assigned_team": "human-support"},
                )
                return current
            if not current.external_key or current.integration_status != "synced":
                raise ValueError("The remote ticket must be synced before escalation.")
            pending = conn.execute(
                "SELECT event_id, status FROM ticket_outbox "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if pending and pending["status"] in {"pending", "in_progress", "succeeded"}:
                self._insert_audit_event(
                    conn,
                    tenant_id=tenant_id,
                    ticket_id=ticket_id,
                    event_type="ticket.escalation.replayed",
                    actor=actor,
                    outcome="replayed",
                    request_id=request_id,
                    idempotency_key=None,
                    details={"status": current.status},
                )
                return current
            if pending:
                conn.execute(
                    "UPDATE ticket_outbox SET status = 'pending', attempt_count = 0, "
                    "next_attempt_at = ?, lease_until = NULL, last_error_code = NULL, "
                    "request_id = ?, actor = ?, updated_at = ? WHERE event_id = ?",
                    (now, request_id, actor, now, pending["event_id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO ticket_outbox (event_id, tenant_id, ticket_id, event_type, "
                    "idempotency_key, request_id, actor, status, attempt_count, "
                    "next_attempt_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'ticket.escalate', ?, ?, ?, 'pending', 0, ?, ?, ?)",
                    (
                        uuid4().hex,
                        tenant_id,
                        ticket_id,
                        idempotency_key,
                        request_id,
                        actor,
                        now,
                        now,
                        now,
                    ),
                )
            conn.execute(
                "UPDATE tickets SET integration_status = 'pending', updated_at = ? "
                "WHERE id = ? AND tenant_id = ?",
                (now, ticket_id, tenant_id),
            )
            self._insert_audit_event(
                conn,
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                event_type="ticket.escalation.requested",
                actor=actor,
                outcome="queued",
                request_id=request_id,
                idempotency_key=None,
                details={"status": "open", "target_team": "human-support"},
            )
        return self.get(ticket_id, tenant_id)

    def claim_outbox_event(self, lease_seconds: int = 30) -> dict[str, object] | None:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        lease_text = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM ticket_outbox WHERE "
                "(status = 'pending' AND next_attempt_at <= ?) OR "
                "(status = 'in_progress' AND lease_until <= ?) "
                "ORDER BY created_at, event_id LIMIT 1",
                (now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE ticket_outbox SET status = 'in_progress', attempt_count = "
                "attempt_count + 1, lease_until = ?, updated_at = ? WHERE event_id = ?",
                (lease_text, now_text, row["event_id"]),
            )
            claimed = conn.execute(
                "SELECT * FROM ticket_outbox WHERE event_id = ?", (row["event_id"],)
            ).fetchone()
        return dict(claimed)

    def complete_outbox_event(
        self,
        event_id: str,
        external_key: str | None = None,
        *,
        external_system: str | None = None,
        notifications_enabled: bool = False,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute(
                "SELECT * FROM ticket_outbox WHERE event_id = ?", (event_id,)
            ).fetchone()
            if event is None:
                return
            ticket = conn.execute(
                "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?",
                (event["ticket_id"], event["tenant_id"]),
            ).fetchone()
            if ticket is None:
                return
            if event["event_type"] == "ticket.create":
                if not external_key or not external_system:
                    raise ValueError("A provider key and system name are required.")
                conn.execute(
                    "UPDATE tickets SET external_system = ?, external_key = ?, "
                    "integration_status = 'synced', updated_at = ? "
                    "WHERE id = ? AND tenant_id = ?",
                    (external_system, external_key, now, event["ticket_id"], event["tenant_id"]),
                )
                audit_type = "integration.sync.succeeded"
                details = {
                    "event_type": event["event_type"],
                    "external_system": external_system,
                    "external_key_present": True,
                }
            elif event["event_type"] == "ticket.escalate":
                conn.execute(
                    "UPDATE tickets SET status = 'escalated', assigned_team = 'human-support', "
                    "integration_status = 'synced', updated_at = ? "
                    "WHERE id = ? AND tenant_id = ?",
                    (now, event["ticket_id"], event["tenant_id"]),
                )
                audit_type = "ticket.escalated"
                details = {
                    "previous_status": str(ticket["status"]),
                    "status": "escalated",
                    "assigned_team": "human-support",
                }
                if notifications_enabled:
                    self._enqueue_slack_event(
                        conn,
                        tenant_id=str(event["tenant_id"]),
                        ticket_id=str(event["ticket_id"]),
                        event_type="slack.ticket_escalated",
                        idempotency_key=(
                            f"slack-escalated:{event['tenant_id']}:{event['ticket_id']}"
                        ),
                        request_id=event["request_id"],
                        actor=str(event["actor"]),
                        now=now,
                    )
            elif str(event["event_type"]).startswith("slack."):
                audit_type = "slack.notification.succeeded"
                details = {"event_type": str(event["event_type"])}
            else:
                raise ValueError("Unsupported ticket outbox event.")
            conn.execute(
                "UPDATE ticket_outbox SET status = 'succeeded', lease_until = NULL, "
                "last_error_code = NULL, updated_at = ? WHERE event_id = ?",
                (now, event_id),
            )
            if str(event["event_type"]).startswith("slack.") or (
                event["event_type"] == "ticket.escalate" and notifications_enabled
            ):
                self._refresh_notification_status(
                    conn, str(event["tenant_id"]), str(event["ticket_id"]), now
                )
            self._insert_audit_event(
                conn,
                tenant_id=event["tenant_id"],
                ticket_id=event["ticket_id"],
                event_type=audit_type,
                actor=event["actor"],
                outcome="success",
                request_id=event["request_id"],
                idempotency_key=event["idempotency_key"],
                details=details,
            )

    def retry_outbox_event(
        self,
        event_id: str,
        *,
        error_code: str,
        max_attempts: int,
        retry_after_seconds: int,
    ) -> bool:
        now = datetime.now(UTC)
        next_attempt = (now + timedelta(seconds=retry_after_seconds)).isoformat()
        now_text = now.isoformat()
        exhausted = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute(
                "SELECT * FROM ticket_outbox WHERE event_id = ?", (event_id,)
            ).fetchone()
            if event is None:
                return True
            exhausted = int(event["attempt_count"]) >= max_attempts
            outbox_status = "failed" if exhausted else "pending"
            is_slack_event = str(event["event_type"]).startswith("slack.")
            integration_status = "failed" if exhausted else "pending"
            conn.execute(
                "UPDATE ticket_outbox SET status = ?, next_attempt_at = ?, lease_until = NULL, "
                "last_error_code = ?, updated_at = ? WHERE event_id = ?",
                (outbox_status, next_attempt, error_code, now_text, event_id),
            )
            if is_slack_event:
                self._refresh_notification_status(
                    conn, str(event["tenant_id"]), str(event["ticket_id"]), now_text
                )
            else:
                conn.execute(
                    "UPDATE tickets SET integration_status = ?, updated_at = ? "
                    "WHERE id = ? AND tenant_id = ?",
                    (integration_status, now_text, event["ticket_id"], event["tenant_id"]),
                )
            self._insert_audit_event(
                conn,
                tenant_id=event["tenant_id"],
                ticket_id=event["ticket_id"],
                event_type=(
                    "slack.notification.failed"
                    if is_slack_event and exhausted
                    else "slack.notification.retry_scheduled"
                    if is_slack_event
                    else "integration.sync.failed"
                    if exhausted
                    else "integration.sync.retry_scheduled"
                ),
                actor=event["actor"],
                outcome="failed" if exhausted else "retry_scheduled",
                request_id=event["request_id"],
                idempotency_key=event["idempotency_key"],
                details={
                    "event_type": event["event_type"],
                    "attempt": int(event["attempt_count"]),
                    "error_code": error_code,
                    "final": exhausted,
                },
            )
        return exhausted

    def set_integration(
        self,
        ticket_id: str,
        *,
        tenant_id: str,
        external_system: str | None,
        external_key: str | None,
        integration_status: str,
        outcome: str,
        request_id: str | None = None,
        actor: str = "integration",
    ) -> TicketRecord | None:
        if integration_status not in {"not_configured", "pending", "synced", "failed"}:
            raise ValueError("Unsupported integration status.")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT id FROM tickets WHERE id = ? AND tenant_id = ?",
                (ticket_id, tenant_id),
            ).fetchone()
            if current is None:
                return None
            conn.execute(
                "UPDATE tickets SET external_system = ?, external_key = ?, "
                "integration_status = ?, updated_at = ? WHERE id = ? AND tenant_id = ?",
                (
                    external_system,
                    external_key,
                    integration_status,
                    datetime.now(UTC).isoformat(),
                    ticket_id,
                    tenant_id,
                ),
            )
            self._insert_audit_event(
                conn,
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                event_type=f"integration.sync.{outcome}",
                actor=actor,
                outcome=outcome,
                request_id=request_id,
                idempotency_key=None,
                details={
                    "external_system": external_system,
                    "integration_status": integration_status,
                    "external_key_present": external_key is not None,
                },
            )
        return self.get(ticket_id, tenant_id)

    def get(self, ticket_id: str, tenant_id: str = "demo") -> TicketRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?", (ticket_id, tenant_id)
            ).fetchone()
        return TicketRecord.model_validate(dict(row)) if row else None

    def get_outbox_summary(self, tenant_id: str = "demo") -> TicketOutboxSummary:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT "
                "SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending, "
                "SUM(CASE WHEN (status = 'pending' AND attempt_count > 0) OR "
                "(status = 'in_progress' AND attempt_count > 1) THEN 1 ELSE 0 END) AS retrying, "
                "SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress, "
                "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
                "SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded, "
                "SUM(CASE WHEN event_type LIKE 'slack.%' AND status = 'pending' "
                "THEN 1 ELSE 0 END) AS notification_pending, "
                "SUM(CASE WHEN event_type LIKE 'slack.%' AND status = 'failed' "
                "THEN 1 ELSE 0 END) AS notification_failed, "
                "SUM(CASE WHEN event_type LIKE 'slack.%' AND status = 'succeeded' "
                "THEN 1 ELSE 0 END) AS notification_succeeded, "
                "MIN(CASE WHEN status IN ('pending', 'in_progress') THEN created_at END) "
                "AS oldest_pending_at FROM ticket_outbox WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return TicketOutboxSummary(
            tenant_id=tenant_id,
            pending=row["pending"] or 0,
            retrying=row["retrying"] or 0,
            in_progress=row["in_progress"] or 0,
            failed=row["failed"] or 0,
            succeeded=row["succeeded"] or 0,
            notification_pending=row["notification_pending"] or 0,
            notification_failed=row["notification_failed"] or 0,
            notification_succeeded=row["notification_succeeded"] or 0,
            oldest_pending_at=row["oldest_pending_at"],
        )

    def list_recent(self, limit: int = 50, tenant_id: str = "demo") -> list[TicketRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [TicketRecord.model_validate(dict(row)) for row in rows]

    def escalate(
        self,
        ticket_id: str,
        tenant_id: str = "demo",
        request_id: str | None = None,
        actor: str = "system",
        notifications_enabled: bool = False,
    ) -> TicketRecord | None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?", (ticket_id, tenant_id)
            ).fetchone()
            if current is None:
                return None
            previous_status = str(current["status"])
            if previous_status == "escalated":
                self._insert_audit_event(
                    conn,
                    tenant_id=tenant_id,
                    ticket_id=ticket_id,
                    event_type="ticket.escalation.replayed",
                    actor=actor,
                    outcome="no_change",
                    request_id=request_id,
                    idempotency_key=None,
                    details={"status": "escalated", "assigned_team": "human-support"},
                )
                return TicketRecord.model_validate(dict(current))
            result = conn.execute(
                "UPDATE tickets SET status = 'escalated', assigned_team = 'human-support', "
                "notification_status = ?, "
                "updated_at = ? WHERE id = ? AND tenant_id = ?",
                (
                    "pending" if notifications_enabled else str(current["notification_status"]),
                    now,
                    ticket_id,
                    tenant_id,
                ),
            )
            if result.rowcount:
                if notifications_enabled:
                    self._enqueue_slack_event(
                        conn,
                        tenant_id=tenant_id,
                        ticket_id=ticket_id,
                        event_type="slack.ticket_escalated",
                        idempotency_key=f"slack-escalated:{tenant_id}:{ticket_id}",
                        request_id=request_id,
                        actor=actor,
                        now=now,
                    )
                    self._refresh_notification_status(conn, tenant_id, ticket_id, now)
                self._insert_audit_event(
                    conn,
                    tenant_id=tenant_id,
                    ticket_id=ticket_id,
                    event_type="ticket.escalated",
                    actor=actor,
                    outcome="success",
                    request_id=request_id,
                    idempotency_key=None,
                    details={
                        "previous_status": previous_status,
                        "status": "escalated",
                        "assigned_team": "human-support",
                    },
                )
        return self.get(ticket_id, tenant_id) if result.rowcount else None

    @staticmethod
    def _enqueue_slack_event(
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        ticket_id: str,
        event_type: str,
        idempotency_key: str,
        request_id: str | None,
        actor: str,
        now: str,
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO ticket_outbox (event_id, tenant_id, ticket_id, event_type, "
            "idempotency_key, request_id, actor, status, attempt_count, next_attempt_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
            (
                uuid4().hex,
                tenant_id,
                ticket_id,
                event_type,
                idempotency_key,
                request_id,
                actor,
                now,
                now,
                now,
            ),
        )

    @staticmethod
    def _refresh_notification_status(
        conn: sqlite3.Connection, tenant_id: str, ticket_id: str, now: str
    ) -> None:
        rows = conn.execute(
            "SELECT status FROM ticket_outbox WHERE tenant_id = ? AND ticket_id = ? "
            "AND event_type LIKE 'slack.%'",
            (tenant_id, ticket_id),
        ).fetchall()
        statuses = {str(row["status"]) for row in rows}
        if "failed" in statuses:
            notification_status = "failed"
        elif statuses.intersection({"pending", "in_progress"}):
            notification_status = "pending"
        elif "succeeded" in statuses:
            notification_status = "synced"
        else:
            notification_status = "not_configured"
        conn.execute(
            "UPDATE tickets SET notification_status = ?, updated_at = ? "
            "WHERE id = ? AND tenant_id = ?",
            (notification_status, now, ticket_id, tenant_id),
        )

    @staticmethod
    def _insert_audit_event(
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        ticket_id: str | None,
        event_type: str,
        actor: str,
        outcome: str,
        request_id: str | None,
        idempotency_key: str | None,
        details: dict[str, str | int | float | bool | None],
    ) -> None:
        created_at = datetime.now(UTC).isoformat()
        fingerprint = (
            hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest() if idempotency_key else None
        )
        conn.execute(
            "INSERT INTO ticket_audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                tenant_id,
                ticket_id,
                event_type,
                actor,
                outcome,
                request_id,
                fingerprint,
                json.dumps(details, sort_keys=True, separators=(",", ":")),
                created_at,
            ),
        )

    def list_audit_events(
        self,
        ticket_id: str,
        tenant_id: str = "demo",
        limit: int = 50,
    ) -> list[TicketAuditEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ticket_audit_events WHERE tenant_id = ? AND ticket_id = ? "
                "ORDER BY created_at DESC, event_id DESC LIMIT ?",
                (tenant_id, ticket_id, limit),
            ).fetchall()
        return [
            TicketAuditEvent(
                event_id=row["event_id"],
                tenant_id=row["tenant_id"],
                ticket_id=row["ticket_id"],
                event_type=row["event_type"],
                actor=row["actor"],
                outcome=row["outcome"],
                request_id=row["request_id"],
                idempotency_fingerprint=row["idempotency_fingerprint"],
                details=json.loads(row["details_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
