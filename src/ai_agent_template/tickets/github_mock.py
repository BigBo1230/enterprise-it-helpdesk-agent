"""GitHub Issues REST client for the local simulator."""

from urllib.parse import quote

from ai_agent_template.core.settings import Settings
from ai_agent_template.tickets.provider_http import MockProviderHttpClient, ProviderRequestError
from ai_agent_template.tickets.schemas import TicketRecord


class GitHubMockClient:
    system_name = "github-mock"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = MockProviderHttpClient(
            settings.github_base_url,
            settings.github_api_token,
            settings.github_timeout_seconds,
        )

    def create_issue(
        self,
        ticket: TicketRecord,
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> str:
        owner = quote(self.settings.github_owner, safe="")
        repo = quote(self.settings.github_repo, safe="")
        response = self.http.request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            body={
                "title": ticket.title,
                "body": ticket.description,
                "priority": ticket.priority,
                "reporter": ticket.requester,
            },
        )
        number = response.get("number")
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, str))
            or not str(number).isdigit()
        ):
            raise ProviderRequestError("invalid_response")
        return f"{self.settings.github_owner}/{self.settings.github_repo}#{number}"

    def escalate(self, issue_key: str, *, tenant_id: str) -> None:
        repository = f"{self.settings.github_owner}/{self.settings.github_repo}"
        prefix, separator, number = issue_key.rpartition("#")
        if not separator or prefix != repository or not number.isdigit():
            raise ProviderRequestError("invalid_external_key")
        owner = quote(self.settings.github_owner, safe="")
        repo = quote(self.settings.github_repo, safe="")
        self.http.request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/labels",
            tenant_id=tenant_id,
            body={"labels": ["human-support", "escalated"]},
        )
