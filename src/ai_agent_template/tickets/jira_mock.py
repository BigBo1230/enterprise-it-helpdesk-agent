"""Jira REST client and ticket adapter for the local simulator."""

from urllib.parse import quote

from ai_agent_template.core.settings import Settings
from ai_agent_template.tickets.provider_http import MockProviderHttpClient, ProviderRequestError
from ai_agent_template.tickets.schemas import TicketRecord


class JiraMockClient:
    system_name = "jira-mock"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = MockProviderHttpClient(
            settings.jira_base_url,
            settings.jira_api_token,
            settings.jira_timeout_seconds,
        )

    def create_issue(
        self,
        ticket: TicketRecord,
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> str:
        response = self.http.request(
            "POST",
            "/rest/api/3/issue",
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            body={
                "project_key": self.settings.jira_project_key,
                "summary": ticket.title,
                "description": ticket.description,
                "priority": ticket.priority,
                "reporter": ticket.requester,
            },
        )
        issue_key = response.get("key")
        if not isinstance(issue_key, str) or not issue_key:
            raise ProviderRequestError("invalid_response")
        return issue_key

    def escalate(self, issue_key: str, *, tenant_id: str) -> None:
        self.http.request(
            "POST",
            f"/rest/api/3/issue/{quote(issue_key, safe='')}/transitions",
            tenant_id=tenant_id,
            body={"transition": "escalate"},
        )
