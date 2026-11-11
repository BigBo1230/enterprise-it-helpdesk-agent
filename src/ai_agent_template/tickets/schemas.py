from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Priority = Literal["P1", "P2", "P3"]
Status = Literal["open", "escalated"]


class TicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=10, max_length=4000)
    priority: Priority = "P3"
    requester: str = Field(min_length=2, max_length=120)


class TicketRecord(TicketCreate):
    id: str
    tenant_id: str
    status: Status
    assigned_team: str
    external_system: str | None = None
    external_key: str | None = None
    integration_status: Literal["not_configured", "pending", "synced", "failed"] = "not_configured"
    notification_status: Literal["not_configured", "pending", "synced", "failed"] = "not_configured"
    created_at: datetime
    updated_at: datetime


class TicketAuditEvent(BaseModel):
    event_id: str
    tenant_id: str
    ticket_id: str | None
    event_type: str
    actor: str
    outcome: str
    request_id: str | None
    idempotency_fingerprint: str | None
    details: dict[str, str | int | float | bool | None]
    created_at: datetime


class TicketOutboxSummary(BaseModel):
    tenant_id: str
    pending: int
    retrying: int
    in_progress: int
    failed: int
    succeeded: int
    notification_pending: int = 0
    notification_failed: int = 0
    notification_succeeded: int = 0
    oldest_pending_at: datetime | None
