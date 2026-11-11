"""Stable ticket backend contract and configuration based factory."""

from typing import Protocol

from ai_agent_template.core.settings import Settings
from ai_agent_template.tickets.schemas import (
    TicketAuditEvent,
    TicketCreate,
    TicketOutboxSummary,
    TicketRecord,
)
from ai_agent_template.tickets.store import TicketStore


class TicketIntegrationError(RuntimeError):
    """A required remote ticket action could not be confirmed."""


class TicketProvider(Protocol):
    system_name: str

    def create_issue(
        self,
        ticket: TicketRecord,
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> str: ...

    def escalate(self, issue_key: str, *, tenant_id: str) -> None: ...


class TicketBackend(Protocol):
    def create(
        self,
        data: TicketCreate,
        tenant_id: str = "demo",
        idempotency_key: str | None = None,
        request_id: str | None = None,
        actor: str = "system",
    ) -> TicketRecord: ...

    def get(self, ticket_id: str, tenant_id: str = "demo") -> TicketRecord | None: ...

    def list_recent(self, limit: int = 50, tenant_id: str = "demo") -> list[TicketRecord]: ...

    def escalate(
        self,
        ticket_id: str,
        tenant_id: str = "demo",
        request_id: str | None = None,
        actor: str = "system",
    ) -> TicketRecord | None: ...

    def list_audit_events(
        self, ticket_id: str, tenant_id: str = "demo", limit: int = 50
    ) -> list[TicketAuditEvent]: ...

    def get_outbox_summary(self, tenant_id: str = "demo") -> TicketOutboxSummary: ...


class TicketBackendAdapter:
    """Keeps the ticket API stable while routing to an optional provider and notifier."""

    def __init__(
        self,
        store: TicketStore,
        provider: TicketProvider | None,
        *,
        notifications_enabled: bool,
    ) -> None:
        self.store = store
        self.provider = provider
        self.notifications_enabled = notifications_enabled

    def create(
        self,
        data: TicketCreate,
        tenant_id: str = "demo",
        idempotency_key: str | None = None,
        request_id: str | None = None,
        actor: str = "system",
    ) -> TicketRecord:
        return self.store.create(
            data,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            actor=actor,
            integration_system=self.provider.system_name if self.provider else None,
            notifications_enabled=self.notifications_enabled,
        )

    def get(self, ticket_id: str, tenant_id: str = "demo") -> TicketRecord | None:
        return self.store.get(ticket_id, tenant_id)

    def list_recent(self, limit: int = 50, tenant_id: str = "demo") -> list[TicketRecord]:
        return self.store.list_recent(limit, tenant_id)

    def list_audit_events(
        self, ticket_id: str, tenant_id: str = "demo", limit: int = 50
    ) -> list[TicketAuditEvent]:
        return self.store.list_audit_events(ticket_id, tenant_id, limit)

    def get_outbox_summary(self, tenant_id: str = "demo") -> TicketOutboxSummary:
        return self.store.get_outbox_summary(tenant_id)

    def escalate(
        self,
        ticket_id: str,
        tenant_id: str = "demo",
        request_id: str | None = None,
        actor: str = "system",
    ) -> TicketRecord | None:
        ticket = self.store.get(ticket_id, tenant_id)
        if ticket is None:
            return None
        if ticket.external_system is None:
            return self.store.escalate(
                ticket_id,
                tenant_id=tenant_id,
                request_id=request_id,
                actor=actor,
                notifications_enabled=self.notifications_enabled,
            )
        if ticket.integration_status != "synced" or not ticket.external_key:
            raise TicketIntegrationError(
                "Wait for provider synchronization before requesting escalation."
            )
        return self.store.enqueue_escalation(
            ticket_id,
            tenant_id=tenant_id,
            request_id=request_id,
            actor=actor,
        )


def build_ticket_provider(
    settings: Settings,
    system_name: str | None = None,
) -> TicketProvider:
    provider_name = system_name or settings.ticket_backend
    if provider_name == "jira_mock":
        from ai_agent_template.tickets.jira_mock import JiraMockClient

        return JiraMockClient(settings)
    if provider_name == "github_mock":
        from ai_agent_template.tickets.github_mock import GitHubMockClient

        return GitHubMockClient(settings)
    raise ValueError("No remote ticket provider is configured.")


def build_ticket_backend(settings: Settings) -> TicketBackend:
    store = TicketStore(settings.ticket_db_path)
    provider = None if settings.ticket_backend == "local" else build_ticket_provider(settings)
    return TicketBackendAdapter(
        store=store,
        provider=provider,
        notifications_enabled=settings.slack_notifications_enabled,
    )
