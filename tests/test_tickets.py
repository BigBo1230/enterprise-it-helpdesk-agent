import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ai_agent_template.core.settings as settings_module
from ai_agent_template.api.app import create_app
from ai_agent_template.api.dependencies import get_app_settings, get_ticket_store
from ai_agent_template.core.settings import Settings
from ai_agent_template.tickets.backend import build_ticket_backend
from ai_agent_template.tickets.schemas import TicketCreate
from ai_agent_template.tickets.store import TicketStore


def test_tenant_keys_isolate_ticket_access_and_migrate_old_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "tickets.sqlite3")
    settings = Settings(
        _env_file=None,
        tenant_api_keys={"demo": "alpha-key", "beta": "beta-key"},
        rate_limit_enabled=False,
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_ticket_store] = lambda: TicketStore(db_path)
    client = TestClient(app)
    payload = {
        "title": "Printer offline",
        "description": "Printer cannot connect to network.",
        "priority": "P3",
        "requester": "employee",
    }
    assert client.get("/v1/tickets").status_code == 401
    a = client.post(
        "/v1/tickets",
        json=payload,
        headers={
            "x-api-key": "alpha-key",
            "Idempotency-Key": "shared-create-key",
            "x-request-id": "create-http-001",
        },
    )
    assert a.status_code == 201
    ticket_id = a.json()["id"]
    assert a.json()["tenant_id"] == "demo"
    replay = client.post(
        "/v1/tickets",
        json=payload,
        headers={
            "x-api-key": "alpha-key",
            "Idempotency-Key": "shared-create-key",
            "x-request-id": "create-http-002",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == ticket_id
    events = client.get(f"/v1/tickets/{ticket_id}/events", headers={"x-api-key": "alpha-key"})
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "ticket.create.replayed"
    assert events.json()[0]["request_id"] == "create-http-002"
    assert events.json()[0]["actor"] == "api-client"
    assert "description" not in events.json()[0]
    assert (
        client.get(f"/v1/tickets/{ticket_id}/events", headers={"x-api-key": "beta-key"}).status_code
        == 404
    )
    changed_payload = {**payload, "title": "A different ticket"}
    conflict = client.post(
        "/v1/tickets",
        json=changed_payload,
        headers={"x-api-key": "alpha-key", "Idempotency-Key": "shared-create-key"},
    )
    assert conflict.status_code == 409
    conflict_events = client.get(
        f"/v1/tickets/{ticket_id}/events", headers={"x-api-key": "alpha-key"}
    ).json()
    assert conflict_events[0]["event_type"] == "ticket.create.idempotency_conflict"
    assert conflict_events[0]["outcome"] == "rejected"
    assert client.get("/v1/tickets", headers={"x-api-key": "beta-key"}).json() == []
    assert (
        client.get(f"/v1/tickets/{ticket_id}", headers={"x-api-key": "beta-key"}).status_code == 404
    )
    assert (
        client.post(
            f"/v1/tickets/{ticket_id}/escalate", headers={"x-api-key": "beta-key"}
        ).status_code
        == 404
    )
    assert (
        client.get(f"/v1/tickets/{ticket_id}", headers={"x-api-key": "alpha-key"}).json()["status"]
        == "open"
    )
    b = client.post(
        "/v1/tickets",
        json=payload,
        headers={"x-api-key": "beta-key", "Idempotency-Key": "shared-create-key"},
    )
    assert b.status_code == 201
    assert b.json()["tenant_id"] == "beta"
    assert len(client.get("/v1/tickets", headers={"x-api-key": "alpha-key"}).json()) == 1


def test_ticket_lifecycle_persists_and_requires_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The dependency is replaced so the test never touches the Docker volume.
    settings = Settings(_env_file=None, auth_api_keys=["test-key"], rate_limit_enabled=False)
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_app_settings] = lambda: settings
    db_path = str(tmp_path / "tickets.sqlite3")
    app.dependency_overrides[get_ticket_store] = lambda: TicketStore(db_path)
    client = TestClient(app)

    payload = {
        "title": "VPN-403 connection failure",
        "description": "VPN client returns VPN-403 after login.",
        "priority": "P2",
        "requester": "employee-001",
    }
    assert (
        client.post(
            "/v1/tickets", json=payload, headers={"Idempotency-Key": "unauth-create"}
        ).status_code
        == 401
    )
    response = client.post(
        "/v1/tickets",
        json=payload,
        headers={"x-api-key": "test-key", "Idempotency-Key": "lifecycle-create"},
    )
    assert response.status_code == 201
    ticket_id = response.json()["id"]
    assert response.json()["status"] == "open"
    listed = client.get("/v1/tickets", headers={"x-api-key": "test-key"})
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == ticket_id

    # A new store instance reads the same ticket from disk.
    assert TicketStore(db_path).get(ticket_id) is not None
    escalated = client.post(
        f"/v1/tickets/{ticket_id}/escalate",
        headers={"x-api-key": "test-key", "x-request-id": "escalate-http-001"},
    )
    assert escalated.status_code == 200
    assert escalated.json()["assigned_team"] == "human-support"
    assert TicketStore(db_path).get(ticket_id).status == "escalated"  # type: ignore[union-attr]
    events = client.get(f"/v1/tickets/{ticket_id}/events", headers={"x-api-key": "test-key"}).json()
    assert events[0]["event_type"] == "ticket.escalated"
    assert events[0]["request_id"] == "escalate-http-001"
    assert events[0]["details"]["previous_status"] == "open"
    replay_escalation = client.post(
        f"/v1/tickets/{ticket_id}/escalate", headers={"x-api-key": "test-key"}
    )
    assert replay_escalation.status_code == 200
    replay_events = client.get(
        f"/v1/tickets/{ticket_id}/events", headers={"x-api-key": "test-key"}
    ).json()
    assert replay_events[0]["event_type"] == "ticket.escalation.replayed"
    assert replay_events[0]["outcome"] == "no_change"


def test_existing_tickets_keep_their_provider_when_default_changes(tmp_path: Path) -> None:
    db_path = str(tmp_path / "provider-switch.sqlite3")
    jira = build_ticket_backend(
        Settings(
            _env_file=None,
            ticket_db_path=db_path,
            ticket_backend="jira_mock",
            slack_notifications_enabled=False,
        )
    )
    payload = TicketCreate(
        title="VPN connection failed",
        description="VPN client reports code 403.",
        priority="P2",
        requester="employee",
    )
    remote_ticket = jira.create(payload, tenant_id="beta", idempotency_key="remote-1")
    with closing(sqlite3.connect(db_path)) as conn:
        event_id = conn.execute(
            "SELECT event_id FROM ticket_outbox WHERE event_type = 'ticket.create'"
        ).fetchone()[0]
    store = TicketStore(db_path)
    store.complete_outbox_event(event_id, external_key="HELP-1", external_system="jira-mock")

    local = build_ticket_backend(
        Settings(
            _env_file=None,
            ticket_db_path=db_path,
            ticket_backend="local",
            slack_notifications_enabled=False,
        )
    )
    queued = local.escalate(remote_ticket.id, tenant_id="beta")
    assert queued is not None
    assert queued.status == "open"
    assert queued.external_system == "jira-mock"
    assert queued.integration_status == "pending"

    local_ticket = local.create(payload, tenant_id="beta", idempotency_key="local-1")
    github = build_ticket_backend(
        Settings(
            _env_file=None,
            ticket_db_path=db_path,
            ticket_backend="github_mock",
            slack_notifications_enabled=False,
        )
    )
    escalated = github.escalate(local_ticket.id, tenant_id="beta")
    assert escalated is not None
    assert escalated.status == "escalated"
    assert escalated.external_system is None


def test_slack_failures_do_not_change_ticket_sync_status(tmp_path: Path) -> None:
    db_path = str(tmp_path / "notifications.sqlite3")
    store = TicketStore(db_path)
    payload = TicketCreate(
        title="VPN connection failed",
        description="VPN client reports code 403.",
        priority="P2",
        requester="employee",
    )
    ticket = store.create(
        payload,
        tenant_id="beta",
        idempotency_key="create-1",
        notifications_enabled=True,
    )
    created_event = store.claim_outbox_event()
    assert created_event is not None
    assert created_event["event_type"] == "slack.ticket_created"
    store.retry_outbox_event(
        str(created_event["event_id"]),
        error_code="network_error",
        max_attempts=1,
        retry_after_seconds=1,
    )
    store.escalate(ticket.id, tenant_id="beta", notifications_enabled=True)
    escalated_event = store.claim_outbox_event()
    assert escalated_event is not None
    assert escalated_event["event_type"] == "slack.ticket_escalated"
    store.complete_outbox_event(str(escalated_event["event_id"]))
    current = store.get(ticket.id, "beta")
    assert current is not None
    assert current.integration_status == "not_configured"
    assert current.notification_status == "failed"

    replay = store.create(
        payload,
        tenant_id="beta",
        idempotency_key="create-1",
        notifications_enabled=True,
    )
    assert replay.id == ticket.id
    assert replay.notification_status == "pending"
    store.complete_outbox_event(str(created_event["event_id"]))
    assert store.get(ticket.id, "beta").notification_status == "synced"  # type: ignore[union-attr]
