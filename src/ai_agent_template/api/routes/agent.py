import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from ai_agent_template.agent.service import AgentService
from ai_agent_template.api.dependencies import get_agent_service
from ai_agent_template.api.schemas import AgentInvokeRequest, AgentInvokeResponse
from ai_agent_template.api.security import get_current_tenant

router = APIRouter()
AgentServiceDependency = Annotated[AgentService, Depends(get_agent_service)]
TenantDependency = Annotated[str, Depends(get_current_tenant)]


@router.post("/invoke", response_model=AgentInvokeResponse)
async def invoke_agent(
    http_request: Request,
    request: AgentInvokeRequest,
    agent_service: AgentServiceDependency,
    tenant_id: TenantDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> AgentInvokeResponse:
    return await agent_service.invoke(
        request,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        request_id=http_request.state.request_id,
    )


@router.post("/stream")
async def stream_agent(
    http_request: Request,
    request: AgentInvokeRequest,
    agent_service: AgentServiceDependency,
    tenant_id: TenantDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> StreamingResponse:
    return StreamingResponse(
        _format_sse(
            agent_service.stream(
                request,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                request_id=http_request.state.request_id,
            )
        ),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            "x-accel-buffering": "no",
        },
    )


async def _format_sse(events: AsyncIterator[dict[str, object]]) -> AsyncIterator[bytes]:
    async for event in events:
        event_type = str(event.get("type", "message"))
        payload = json.dumps({k: v for k, v in event.items() if k != "type"}, default=str)
        yield f"event: {event_type}\ndata: {payload}\n\n".encode()
