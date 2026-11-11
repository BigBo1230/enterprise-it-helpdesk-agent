from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from ai_agent_template.api.dependencies import get_ticket_store
from ai_agent_template.api.security import get_current_tenant
from ai_agent_template.tickets.backend import TicketBackend, TicketIntegrationError
from ai_agent_template.tickets.schemas import (
    TicketAuditEvent,
    TicketCreate,
    TicketOutboxSummary,
    TicketRecord,
)
from ai_agent_template.tickets.store import IdempotencyConflictError

router = APIRouter()
TicketStoreDependency = Annotated[TicketBackend, Depends(get_ticket_store)]
TenantDependency = Annotated[str, Depends(get_current_tenant)]


@router.post("", response_model=TicketRecord, status_code=status.HTTP_201_CREATED)
def create_ticket(
    http_request: Request,
    request: TicketCreate,
    store: TicketStoreDependency,
    tenant_id: TenantDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> TicketRecord:
    try:
        return store.create(
            request,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            request_id=http_request.state.request_id,
            actor="api-client",
        )
    except IdempotencyConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=list[TicketRecord])
def list_tickets(
    store: TicketStoreDependency,
    tenant_id: TenantDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TicketRecord]:
    return store.list_recent(limit, tenant_id=tenant_id)


@router.get("/integration-summary", response_model=TicketOutboxSummary)
def get_integration_summary(
    store: TicketStoreDependency,
    tenant_id: TenantDependency,
) -> TicketOutboxSummary:
    return store.get_outbox_summary(tenant_id)


@router.get("/{ticket_id}/events", response_model=list[TicketAuditEvent])
def list_ticket_events(
    ticket_id: str,
    store: TicketStoreDependency,
    tenant_id: TenantDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TicketAuditEvent]:
    if store.get(ticket_id, tenant_id=tenant_id) is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return store.list_audit_events(ticket_id, tenant_id=tenant_id, limit=limit)


@router.get("/{ticket_id}", response_model=TicketRecord)
def get_ticket(
    ticket_id: str, store: TicketStoreDependency, tenant_id: TenantDependency
) -> TicketRecord:
    ticket = store.get(ticket_id, tenant_id=tenant_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return ticket


@router.post("/{ticket_id}/escalate", response_model=TicketRecord)
def escalate_ticket(
    ticket_id: str,
    http_request: Request,
    store: TicketStoreDependency,
    tenant_id: TenantDependency,
) -> TicketRecord:
    try:
        ticket = store.escalate(
            ticket_id,
            tenant_id=tenant_id,
            request_id=http_request.state.request_id,
            actor="api-client",
        )
    except TicketIntegrationError as error:
        raise HTTPException(status_code=503, detail="Ticket integration is unavailable.") from error
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return ticket
