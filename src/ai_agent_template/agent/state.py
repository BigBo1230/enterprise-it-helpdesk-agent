from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from ai_agent_template.rag.schemas import RagSource


class AgentState(TypedDict):
    tenant_id: str
    idempotency_key: str | None
    request_id: str | None
    messages: Annotated[list[BaseMessage], add_messages]
    sources: list[RagSource]
    voice_samples: list[RagSource]


class AgentStateUpdate(TypedDict, total=False):
    """Partial state returned by one LangGraph node."""

    messages: Annotated[list[BaseMessage], add_messages]
    sources: list[RagSource]
    voice_samples: list[RagSource]
