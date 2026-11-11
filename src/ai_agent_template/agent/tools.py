"""Ticket tools available to the helpdesk agent."""

import re
from typing import Annotated

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from loguru import logger

from ai_agent_template.core.settings import get_settings as get_settings
from ai_agent_template.tickets.backend import TicketBackend, build_ticket_backend
from ai_agent_template.tickets.schemas import TicketCreate
from ai_agent_template.tickets.store import IdempotencyConflictError


def _store() -> TicketBackend:
    return build_ticket_backend(get_settings())


def _latest_user_message(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _explicit_ticket_creation(text: str) -> bool:
    return bool(
        re.search(
            r"(帮我|请|麻烦).{0,12}(创建|新建|提交).{0,16}(工单|ticket)|"
            r"(创建|新建|提交)(一个|一张)?(vpn|故障|工单)|"
            r"\b(create|open|submit|raise)\b.{0,30}\b(ticket|incident)\b",
            text,
            re.IGNORECASE,
        )
    )


def _explicit_escalation(text: str) -> bool:
    return bool(
        re.search(
            r"(升级.{0,30}(人工|human|support)|转人工|人工支持)|"
            r"\bescalate\b.{0,40}\b(ticket|human|support)\b",
            text,
            re.IGNORECASE,
        )
    )


@tool
def create_ticket(
    title: str,
    description: str,
    tenant_id: Annotated[str, InjectedState("tenant_id")],
    idempotency_key: Annotated[str | None, InjectedState("idempotency_key")],
    request_id: Annotated[str | None, InjectedState("request_id")],
    messages: Annotated[list[BaseMessage], InjectedState("messages")],
    priority: str = "P3",
    requester: str = "chat-user",
) -> str:
    """Create a helpdesk ticket only when the user explicitly asks to create/report one.
    Priority must be P1, P2, or P3. Return the created ticket and ID."""
    if not _explicit_ticket_creation(_latest_user_message(messages)):
        return (
            "Ticket creation was blocked: the latest user message did not explicitly "
            "request a new ticket."
        )
    try:
        ticket = _store().create(
            TicketCreate(
                title=title, description=description, priority=priority, requester=requester
            ),
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            actor="agent",
        )
        return ticket.model_dump_json()
    except IdempotencyConflictError:
        return (
            "This request key was already used for different ticket details. No second ticket "
            "was created. Check the recent ticket list before submitting with a new key."
        )
    except Exception as error:
        logger.warning("ticket_create_failed", error_type=type(error).__name__)
        return (
            "Ticket service is unavailable. The creation result is unconfirmed: the ticket may "
            "or may not have been created. You may retry at most once with this same "
            "Idempotency-Key; never use a new key for the retry. If still unconfirmed, check "
            "the ticket list. Do not claim success or invent an ID."
        )


@tool
def get_ticket(ticket_id: str, tenant_id: Annotated[str, InjectedState("tenant_id")]) -> str:
    """Look up one helpdesk ticket by ID to report its current status."""
    try:
        ticket = _store().get(ticket_id, tenant_id=tenant_id)
        return ticket.model_dump_json() if ticket else f"No ticket found with ID {ticket_id}."
    except Exception as error:
        logger.warning("ticket_lookup_failed", error_type=type(error).__name__)
        return "Ticket service is unavailable; the ticket status could not be confirmed."


@tool
def list_tickets(tenant_id: Annotated[str, InjectedState("tenant_id")], limit: int = 10) -> str:
    """List recent tickets when the user asks what exists or requests their own tickets."""
    try:
        tickets = _store().list_recent(max(1, min(limit, 50)), tenant_id=tenant_id)
        return "\n".join(ticket.model_dump_json() for ticket in tickets) or "No tickets found."
    except Exception as error:
        logger.warning("ticket_list_failed", error_type=type(error).__name__)
        return "Ticket service is unavailable; the ticket list could not be confirmed."


@tool
def escalate_ticket(
    ticket_id: str,
    tenant_id: Annotated[str, InjectedState("tenant_id")],
    request_id: Annotated[str | None, InjectedState("request_id")],
    messages: Annotated[list[BaseMessage], InjectedState("messages")],
) -> str:
    """Escalate a ticket to human support only when the user explicitly requests a human."""
    if not _explicit_escalation(_latest_user_message(messages)):
        return (
            "Ticket escalation was blocked: the latest user message did not explicitly request "
            "human support."
        )
    try:
        ticket = _store().escalate(
            ticket_id, tenant_id=tenant_id, request_id=request_id, actor="agent"
        )
        return ticket.model_dump_json() if ticket else f"No ticket found with ID {ticket_id}."
    except Exception as error:
        logger.warning("ticket_escalation_failed", error_type=type(error).__name__)
        return "Ticket service is unavailable; escalation could not be confirmed."


TICKET_TOOLS = [create_ticket, get_ticket, list_tickets, escalate_ticket]
