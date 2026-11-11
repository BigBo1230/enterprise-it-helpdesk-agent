import json
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from ai_agent_template.agent import tools
from ai_agent_template.core.settings import Settings
from ai_agent_template.tickets.store import TicketStore


def _tool_function(tool: object) -> Any:
    function = cast(StructuredTool, tool).func
    assert function is not None
    return function


def test_retrieved_injection_cannot_authorize_ticket_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = json.loads(
        (Path(__file__).parent / "evals" / "security.jsonl").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(
        tools,
        "get_settings",
        lambda: Settings(_env_file=None, ticket_db_path=str(tmp_path / "tickets.sqlite3")),
    )
    # Simulate a model incorrectly attempting the write tool after seeing the injected RAG text.
    result = _tool_function(tools.create_ticket)(
        title="P1 injection test",
        description=case["retrieved_text"],
        tenant_id="beta",
        messages=[HumanMessage(content=case["user_message"])],
        priority="P1",
        requester="eval-user",
        idempotency_key="injection-case",
        request_id="eval-injection-request",
    )
    count = len(TicketStore(str(tmp_path / "tickets.sqlite3")).list_recent(tenant_id="beta"))
    assert "blocked" in result.lower()
    assert count == case["expected_ticket_count_delta"]


def test_explicit_latest_user_request_can_create_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "tickets.sqlite3")
    monkeypatch.setattr(
        tools, "get_settings", lambda: Settings(_env_file=None, ticket_db_path=db_path)
    )
    result = _tool_function(tools.create_ticket)(
        title="VPN connection failure",
        description="VPN cannot connect from the test laptop.",
        tenant_id="beta",
        messages=[HumanMessage(content="请帮我创建一个VPN故障工单")],
        priority="P2",
        requester="eval-user",
        idempotency_key="explicit-create-case",
        request_id="eval-explicit-request",
    )
    assert '"tenant_id":"beta"' in result
    store = TicketStore(db_path)
    tickets = store.list_recent(tenant_id="beta")
    assert len(tickets) == 1
    events = store.list_audit_events(tickets[0].id, tenant_id="beta")
    assert events[0].actor == "agent"
    assert events[0].request_id == "eval-explicit-request"
