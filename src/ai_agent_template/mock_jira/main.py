"""Small tenant-aware Jira REST simulator backed by SQLite."""

import hashlib
import hmac
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

DB_PATH = os.getenv("MOCK_JIRA_DB_PATH", "/data/jira_mock.sqlite3")
API_TOKEN = os.getenv("MOCK_JIRA_API_TOKEN", "mock-jira-dev-token")


class IssueCreate(BaseModel):
    project_key: str = Field(min_length=2, max_length=12)
    summary: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=10, max_length=4000)
    priority: Literal["P1", "P2", "P3"]
    reporter: str = Field(min_length=2, max_length=120)


class TransitionRequest(BaseModel):
    transition: Literal["escalate"]


class JiraMockStore:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS issues (
                key TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_key TEXT NOT NULL,
                summary TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL,
                reporter TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tenant_id, key)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS idempotency (
                tenant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                issue_key TEXT NOT NULL,
                PRIMARY KEY (tenant_id, idempotency_key)
            )""")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, payload: IssueCreate, tenant_id: str, idempotency_key: str) -> dict[str, str]:
        data = payload.model_dump(mode="json")
        request_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT request_hash, issue_key FROM idempotency "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(status_code=409, detail="Idempotency key conflict.")
                row = conn.execute(
                    "SELECT * FROM issues WHERE tenant_id = ? AND key = ?",
                    (tenant_id, existing["issue_key"]),
                ).fetchone()
                return dict(row)
            key = f"{payload.project_key.upper()}-{uuid4().hex[:8].upper()}"
            conn.execute(
                "INSERT INTO issues (key, tenant_id, project_key, summary, description, "
                "priority, reporter) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    tenant_id,
                    payload.project_key.upper(),
                    payload.summary,
                    payload.description,
                    payload.priority,
                    payload.reporter,
                ),
            )
            conn.execute(
                "INSERT INTO idempotency VALUES (?, ?, ?, ?)",
                (tenant_id, idempotency_key, request_hash, key),
            )
            return dict(conn.execute("SELECT * FROM issues WHERE key = ?", (key,)).fetchone())

    def get(self, issue_key: str, tenant_id: str) -> dict[str, str] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM issues WHERE tenant_id = ? AND key = ?",
                (tenant_id, issue_key),
            ).fetchone()
        return dict(row) if row else None

    def escalate(self, issue_key: str, tenant_id: str) -> dict[str, str] | None:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE issues SET status = 'escalated' WHERE tenant_id = ? AND key = ?",
                (tenant_id, issue_key),
            )
            if not cursor.rowcount:
                return None
        return self.get(issue_key, tenant_id)


store = JiraMockStore(DB_PATH)
app = FastAPI(title="Jira Mock", version="1.0.0")


def authorize(
    authorization: Annotated[str | None, Header()] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> str:
    expected = f"Bearer {API_TOKEN}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized.")
    if not tenant_id or len(tenant_id) > 64:
        raise HTTPException(status_code=400, detail="X-Tenant-ID is required.")
    return tenant_id


Tenant = Annotated[str, Depends(authorize)]


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/rest/api/3/issue", status_code=status.HTTP_201_CREATED)
def create_issue(
    request: IssueCreate,
    tenant_id: Tenant,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> dict[str, str]:
    return store.create(request, tenant_id, idempotency_key)


@app.get("/rest/api/3/issue/{issue_key}")
def get_issue(issue_key: str, tenant_id: Tenant) -> dict[str, str]:
    issue = store.get(issue_key, tenant_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return issue


@app.post("/rest/api/3/issue/{issue_key}/transitions")
def transition_issue(
    issue_key: str, request: TransitionRequest, tenant_id: Tenant
) -> dict[str, str]:
    issue = store.escalate(issue_key, tenant_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return issue
