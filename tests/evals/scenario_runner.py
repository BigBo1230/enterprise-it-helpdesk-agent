"""Offline side-effect and tenant-boundary checks driven by customer scenarios.

Run inside the API image:
    uv run python -m tests.evals.scenario_runner

This intentionally grades tool safety, not the naturalness of a model answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from ai_agent_template.agent import tools
from ai_agent_template.core.settings import Settings
from ai_agent_template.tickets.schemas import TicketCreate
from ai_agent_template.tickets.store import TicketStore

SCENARIOS = Path(__file__).with_name("customer_scenarios.jsonl")


def _tool_function(tool: object) -> Any:
    function = cast(StructuredTool, tool).func
    assert function is not None
    return function


def main() -> int:
    cases = [
        json.loads(line)
        for line in SCENARIOS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    failures: list[str] = []
    with TemporaryDirectory(prefix="helpdesk-scenarios-") as temp_dir:
        db_path = str(Path(temp_dir) / "tickets.sqlite3")
        settings = Settings(_env_file=None, ticket_db_path=db_path)
        original_get_settings = tools.get_settings
        original_store = tools._store
        tools.get_settings = cast(Any, lambda: settings)
        store = TicketStore(db_path)
        demo_ticket = store.create(
            TicketCreate(
                title="Demo private ticket",
                description="This ticket belongs to the demo tenant only.",
                priority="P3",
                requester="demo-user",
            ),
            tenant_id="demo",
        )

        try:
            for case in cases:
                category = case["category"]
                message = [HumanMessage(content=case["customer_message"])]
                scenario_id = case["id"]
                before = len(store.list_recent(limit=100, tenant_id="beta"))

                if category == "authorization":
                    result = _tool_function(tools.get_ticket)(
                        ticket_id=demo_ticket.id,
                        tenant_id="beta",
                    )
                    passed = "No ticket found" in result
                elif category == "tool-truthfulness":
                    result = _tool_function(tools.get_ticket)(
                        ticket_id="IT-NOT-REAL",
                        tenant_id="beta",
                    )
                    passed = "No ticket found" in result
                elif category == "tool-failure":

                    def fail_store() -> TicketStore:
                        raise TimeoutError("simulated ticket service timeout")

                    tools._store = fail_store
                    try:
                        result = _tool_function(tools.create_ticket)(
                            title="VPN error",
                            description="VPN returns VPN-403 and cannot connect.",
                            tenant_id="beta",
                            messages=message,
                            priority="P2",
                            requester="eval-user",
                            idempotency_key=f"scenario-{scenario_id}",
                            request_id=f"request-{scenario_id}",
                        )
                        after = len(store.list_recent(limit=100, tenant_id="beta"))
                        passed = (
                            "unconfirmed" in result.lower()
                            and "may or may not have been created" in result.lower()
                            and before == after == 0
                        )
                    finally:
                        tools._store = original_store
                else:
                    result = _tool_function(tools.create_ticket)(
                        title="Unexpected write attempt",
                        description="This write is simulated and should be blocked.",
                        tenant_id="beta",
                        messages=message,
                        priority="P1",
                        requester="eval-user",
                        idempotency_key=f"scenario-{scenario_id}",
                        request_id=f"request-{scenario_id}",
                    )
                    after = len(store.list_recent(limit=100, tenant_id="beta"))
                    passed = "blocked" in result.lower() and before == after == 0

                print(f"[{'PASS' if passed else 'FAIL'}] {scenario_id}")
                if not passed:
                    failures.append(scenario_id)
        finally:
            tools.get_settings = original_get_settings
            tools._store = original_store

        demo_count = len(store.list_recent(limit=100, tenant_id="demo"))
        beta_count = len(store.list_recent(limit=100, tenant_id="beta"))
        if demo_count != 1 or beta_count != 0:
            failures.append("tenant-database-state")
            print(f"[FAIL] tenant-database-state demo={demo_count} beta={beta_count}")

    print(f"\n{len(cases)} customer scenarios checked; {len(failures)} safety failures.")
    print("Natural-language response quality is not graded by this offline runner.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
