"""Slack-compatible notification client for the local simulator."""

from ai_agent_template.core.settings import Settings
from ai_agent_template.tickets.provider_http import MockProviderHttpClient, ProviderRequestError
from ai_agent_template.tickets.schemas import TicketRecord


class SlackMockClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = MockProviderHttpClient(
            settings.slack_mock_base_url,
            settings.slack_mock_api_token,
            settings.slack_mock_timeout_seconds,
        )

    def notify(
        self,
        ticket: TicketRecord,
        *,
        tenant_id: str,
        event_type: str,
        idempotency_key: str,
    ) -> None:
        if event_type == "slack.ticket_created":
            text = f"New IT ticket {ticket.id}: {ticket.title} ({ticket.priority})"
        elif event_type == "slack.ticket_escalated":
            text = f"Human support requested for {ticket.id}: {ticket.title}"
        else:
            raise ProviderRequestError("unsupported_notification")

        self.http.request(
            "POST",
            "/api/notifications",
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            body={
                "channel": self.settings.slack_mock_channel,
                "event_type": event_type,
                "ticket_id": ticket.id,
                "title": ticket.title,
                "priority": ticket.priority,
                "status": "open" if event_type == "slack.ticket_created" else "escalated",
                "external_key": (
                    None if event_type == "slack.ticket_created" else ticket.external_key
                ),
                "text": text,
            },
        )
