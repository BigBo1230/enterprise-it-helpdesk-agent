from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from ai_agent_template.agent.service import AgentService
from ai_agent_template.api.schemas import AgentInvokeRequest
from ai_agent_template.rag.schemas import RagSource


class FakeGraph:
    def __init__(self) -> None:
        self.last_input: dict[str, Any] | None = None

    async def ainvoke(
        self,
        input: dict[str, Any],  # noqa: A002 - match LangGraph/LangChain runnable API.
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.last_input = input
        assert input["messages"][0].content == "hello"
        assert config == {"configurable": {"thread_id": "demo:thread-1"}}
        return {"messages": [*input["messages"], AIMessage(content="world")], "sources": []}

    async def astream(
        self,
        input: dict[str, Any],  # noqa: A002 - match LangGraph/LangChain runnable API.
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        yield (
            "updates",
            {"retrieve": {"sources": [RagSource(source="kb", content="x")]}},
        )
        yield ("messages", (AIMessageChunk(content="hello "), {"langgraph_node": "agent"}))
        yield ("messages", (AIMessageChunk(content="world"), {"langgraph_node": "agent"}))


@pytest.mark.asyncio
async def test_agent_service_invokes_graph() -> None:
    service = AgentService(FakeGraph())

    response = await service.invoke(AgentInvokeRequest(message="hello", thread_id="thread-1"))

    assert response.message == "world"
    assert response.thread_id == "thread-1"
    assert response.sources == []


@pytest.mark.asyncio
async def test_agent_service_carries_request_context_into_graph() -> None:
    graph = FakeGraph()
    service = AgentService(graph)

    await service.invoke(
        AgentInvokeRequest(message="hello", thread_id="thread-1"),
        tenant_id="demo",
        idempotency_key="stable-operation-key",
        request_id="http-request-123",
    )

    assert graph.last_input is not None
    assert graph.last_input["tenant_id"] == "demo"
    assert graph.last_input["idempotency_key"] == "stable-operation-key"
    assert graph.last_input["request_id"] == "http-request-123"


@pytest.mark.asyncio
async def test_agent_service_streams_deltas_and_sources() -> None:
    service = AgentService(FakeGraph())

    events = [event async for event in service.stream(AgentInvokeRequest(message="hi"))]

    types = [event["type"] for event in events]
    assert types[0] == "sources"
    assert events[0]["sources"][0]["source"] == "kb"
    assert "delta" in types
    assert types[-1] == "done"
