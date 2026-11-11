"""Poll and deliver ticket integration events from the transactional outbox."""

import time
from typing import Any, cast

from loguru import logger

from ai_agent_template.core.settings import Settings, get_settings
from ai_agent_template.tickets.backend import build_ticket_provider
from ai_agent_template.tickets.provider_http import ProviderRequestError
from ai_agent_template.tickets.store import TicketStore

MAX_ATTEMPTS = 8
LEASE_SECONDS = 30


def process_one(store: TicketStore, settings: Settings) -> bool:
    event = store.claim_outbox_event(lease_seconds=LEASE_SECONDS)
    if event is None:
        return False
    event_id = str(event["event_id"])
    tenant_id = str(event["tenant_id"])
    ticket_id = str(event["ticket_id"])
    event_type = str(event["event_type"])
    attempt = cast(int, event["attempt_count"])
    try:
        ticket = store.get(ticket_id, tenant_id)
        if ticket is None:
            raise ProviderRequestError("local_ticket_missing")
        if event_type.startswith("slack."):
            from ai_agent_template.tickets.slack_mock import SlackMockClient

            SlackMockClient(settings).notify(
                ticket,
                tenant_id=tenant_id,
                event_type=event_type,
                idempotency_key=str(event["idempotency_key"]),
            )
            store.complete_outbox_event(event_id)
        elif event_type == "ticket.create":
            if not ticket.external_system:
                raise ProviderRequestError("provider_not_configured")
            client = build_ticket_provider(settings, ticket.external_system)
            issue_key = client.create_issue(
                ticket,
                tenant_id=tenant_id,
                idempotency_key=str(event["idempotency_key"]),
            )
            store.complete_outbox_event(
                event_id,
                external_key=issue_key,
                external_system=client.system_name,
            )
        elif event_type == "ticket.escalate":
            if not ticket.external_system:
                raise ProviderRequestError("provider_not_configured")
            client = build_ticket_provider(settings, ticket.external_system)
            if not ticket.external_key:
                raise ProviderRequestError("external_issue_missing")
            client.escalate(ticket.external_key, tenant_id=tenant_id)
            store.complete_outbox_event(
                event_id,
                notifications_enabled=settings.slack_notifications_enabled,
            )
        else:
            raise ProviderRequestError("unsupported_event")
        logger.info(
            "ticket_outbox_delivered",
            event_id=event_id,
            ticket_id=ticket_id,
            event_type=event_type,
            attempt=attempt,
        )
    except ProviderRequestError as error:
        _schedule_retry(store, event, error.code)
    except Exception as error:
        _schedule_retry(store, event, f"worker_{type(error).__name__.lower()}")
    return True


def _schedule_retry(
    store: TicketStore,
    event: dict[str, Any],
    error_code: str,
) -> None:
    attempt = int(event["attempt_count"])
    delay = min(2**attempt, 300)
    exhausted = store.retry_outbox_event(
        str(event["event_id"]),
        error_code=error_code,
        max_attempts=MAX_ATTEMPTS,
        retry_after_seconds=delay,
    )
    logger.warning(
        "ticket_outbox_delivery_failed",
        event_id=event["event_id"],
        ticket_id=event["ticket_id"],
        event_type=event["event_type"],
        attempt=attempt,
        retry_after_seconds=delay,
        final=exhausted,
        error_code=error_code,
    )


def run() -> None:
    settings = get_settings()
    store = TicketStore(settings.ticket_db_path)
    logger.info("ticket_outbox_worker_started", service=settings.service_name)
    while True:
        try:
            processed = process_one(store, settings)
            if not processed:
                time.sleep(0.5)
        except Exception as error:
            logger.error("ticket_outbox_worker_loop_failed", error_type=type(error).__name__)
            time.sleep(1)


if __name__ == "__main__":
    run()
