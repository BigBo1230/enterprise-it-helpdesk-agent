"""Tenant-aware local simulator for the GitHub Issues REST API."""

import hashlib
import hmac
import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

DB_PATH = os.getenv("MOCK_GITHUB_DB_PATH", "/data/github_mock.sqlite3")
API_TOKEN = os.getenv("MOCK_GITHUB_API_TOKEN", "mock-github-dev-token")


class IssueCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=10, max_length=4000)
    priority: Literal["P1", "P2", "P3"]
    reporter: str = Field(min_length=2, max_length=120)


class LabelsUpdate(BaseModel):
    labels: list[Literal["human-support", "escalated"]] = Field(min_length=1)


class GitHubMockStore:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS issues (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                priority TEXT NOT NULL,
                reporter TEXT NOT NULL,
                labels_json TEXT NOT NULL DEFAULT '[]',
                state TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tenant_id, owner, repo, number)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS idempotency (
                tenant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                issue_id TEXT NOT NULL,
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

    @staticmethod
    def serialize(row: sqlite3.Row, owner: str, repo: str) -> dict[str, object]:
        return {
            "id": row["id"],
            "number": row["number"],
            "html_url": f"http://localhost:8091/{owner}/{repo}/issues/{row['number']}",
            "title": row["title"],
            "body": row["body"],
            "priority": row["priority"],
            "reporter": row["reporter"],
            "labels": json.loads(row["labels_json"]),
            "state": row["state"],
        }

    def create(
        self,
        owner: str,
        repo: str,
        payload: IssueCreate,
        tenant_id: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        body = payload.model_dump(mode="json")
        request_hash = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT request_hash, issue_id FROM idempotency "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise HTTPException(status_code=409, detail="Idempotency key conflict.")
                row = conn.execute(
                    "SELECT * FROM issues WHERE tenant_id = ? AND id = ?",
                    (tenant_id, prior["issue_id"]),
                ).fetchone()
                return self.serialize(row, owner, repo)
            last = conn.execute(
                "SELECT MAX(number) AS last_number FROM issues "
                "WHERE tenant_id = ? AND owner = ? AND repo = ?",
                (tenant_id, owner, repo),
            ).fetchone()
            number = int(last["last_number"] or 0) + 1
            issue_id = uuid4().hex
            conn.execute(
                "INSERT INTO issues (id, tenant_id, owner, repo, number, title, body, "
                "priority, reporter) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue_id,
                    tenant_id,
                    owner,
                    repo,
                    number,
                    payload.title,
                    payload.body,
                    payload.priority,
                    payload.reporter,
                ),
            )
            conn.execute(
                "INSERT INTO idempotency VALUES (?, ?, ?, ?)",
                (tenant_id, idempotency_key, request_hash, issue_id),
            )
            row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        return self.serialize(row, owner, repo)

    def get(self, owner: str, repo: str, number: int, tenant_id: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM issues WHERE tenant_id = ? AND owner = ? AND repo = ? "
                "AND number = ?",
                (tenant_id, owner, repo, number),
            ).fetchone()
        return self.serialize(row, owner, repo) if row else None

    def add_labels(
        self, owner: str, repo: str, number: int, tenant_id: str, labels: Sequence[str]
    ) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM issues WHERE tenant_id = ? AND owner = ? AND repo = ? "
                "AND number = ?",
                (tenant_id, owner, repo, number),
            ).fetchone()
            if row is None:
                return None
            existing = set(json.loads(row["labels_json"]))
            existing.update(labels)
            conn.execute(
                "UPDATE issues SET labels_json = ? WHERE id = ? AND tenant_id = ?",
                (json.dumps(sorted(existing)), row["id"], tenant_id),
            )
        return self.get(owner, repo, number, tenant_id)


store = GitHubMockStore(DB_PATH)
app = FastAPI(title="GitHub Issues Mock", version="1.0.0")


def authorize(
    authorization: Annotated[str | None, Header()] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> str:
    if authorization is None or not hmac.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized.")
    if not tenant_id or len(tenant_id) > 64:
        raise HTTPException(status_code=400, detail="X-Tenant-ID is required.")
    return tenant_id


Tenant = Annotated[str, Depends(authorize)]


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/repos/{owner}/{repo}/issues", status_code=status.HTTP_201_CREATED)
def create_issue(
    owner: str,
    repo: str,
    request: IssueCreate,
    tenant_id: Tenant,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> dict[str, object]:
    return store.create(owner, repo, request, tenant_id, idempotency_key)


@app.get("/repos/{owner}/{repo}/issues/{number}")
def get_issue(owner: str, repo: str, number: int, tenant_id: Tenant) -> dict[str, object]:
    issue = store.get(owner, repo, number, tenant_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return issue


@app.post("/repos/{owner}/{repo}/issues/{number}/labels")
def add_labels(
    owner: str,
    repo: str,
    number: int,
    request: LabelsUpdate,
    tenant_id: Tenant,
) -> dict[str, object]:
    issue = store.add_labels(owner, repo, number, tenant_id, request.labels)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return issue
