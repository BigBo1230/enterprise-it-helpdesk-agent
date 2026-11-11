"""Tenant-scoped Slack notification simulator backed by SQLite."""

import hashlib
import hmac
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

DB_PATH = os.getenv("MOCK_SLACK_DB_PATH", "/data/slack_mock.sqlite3")
API_TOKEN = os.getenv("MOCK_SLACK_API_TOKEN", "mock-slack-dev-token")


class NotificationCreate(BaseModel):
    channel: str = Field(min_length=1, max_length=80)
    event_type: Literal["slack.ticket_created", "slack.ticket_escalated"]
    ticket_id: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=3, max_length=160)
    priority: Literal["P1", "P2", "P3"]
    status: Literal["open", "escalated"]
    external_key: str | None = Field(default=None, max_length=160)
    text: str = Field(min_length=1, max_length=500)


class SlackMockStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS notifications ("
                "id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, channel TEXT NOT NULL, "
                "event_type TEXT NOT NULL, ticket_id TEXT NOT NULL, title TEXT NOT NULL, "
                "priority TEXT NOT NULL, status TEXT NOT NULL, external_key TEXT, "
                "text TEXT NOT NULL, idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL, "
                "created_at TEXT NOT NULL, UNIQUE (tenant_id, idempotency_key))"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def serialize(row: sqlite3.Row) -> dict[str, object]:
        result: dict[str, object] = dict(row)
        result.pop("payload_hash", None)
        result.pop("idempotency_key", None)
        return result

    def create(
        self,
        tenant_id: str,
        idempotency_key: str,
        payload: NotificationCreate,
    ) -> dict[str, object]:
        payload_json = payload.model_dump_json()
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT * FROM notifications WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency key was reused with a different notification.",
                    )
                return self.serialize(prior)
            notification_id = uuid4().hex
            values = payload.model_dump()
            conn.execute(
                "INSERT INTO notifications (id, tenant_id, channel, event_type, ticket_id, "
                "title, priority, status, external_key, text, idempotency_key, payload_hash, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    notification_id,
                    tenant_id,
                    values["channel"],
                    values["event_type"],
                    values["ticket_id"],
                    values["title"],
                    values["priority"],
                    values["status"],
                    values["external_key"],
                    values["text"],
                    idempotency_key,
                    payload_hash,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM notifications WHERE id = ?", (notification_id,)
            ).fetchone()
        return self.serialize(row)

    def list_recent(self, tenant_id: str, limit: int) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE tenant_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [self.serialize(row) for row in rows]


store = SlackMockStore(DB_PATH)
app = FastAPI(title="Slack Notifications Mock", version="1.0.0")


def authorize(
    authorization: Annotated[str | None, Header()] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> str:
    if authorization is None or not hmac.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized.")
    if not tenant_id or len(tenant_id) > 64:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant required.")
    return tenant_id


Tenant = Annotated[str, Depends(authorize)]


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/notifications", status_code=status.HTTP_201_CREATED)
def create_notification(
    request: NotificationCreate,
    tenant_id: Tenant,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> dict[str, object]:
    return store.create(tenant_id, idempotency_key, request)


@app.get("/api/notifications")
def list_notifications(
    tenant_id: Tenant,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[dict[str, object]]:
    return store.list_recent(tenant_id, limit)
