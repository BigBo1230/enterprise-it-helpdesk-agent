from hmac import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from ai_agent_template.api.dependencies import get_app_settings
from ai_agent_template.core.settings import Settings

API_KEY_HEADER = "x-api-key"

_api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


async def require_api_key(
    request: Request,
    api_key: Annotated[str | None, Depends(_api_key_scheme)] = None,
    settings: Annotated[Settings, Depends(get_app_settings)] = None,  # type: ignore[assignment]
) -> str:
    for tenant_id, configured_key in settings.tenant_api_keys.items():
        if api_key and compare_digest(api_key, configured_key):
            request.state.tenant_id = tenant_id
            request.state.api_key_id = _api_key_label(api_key)
            return tenant_id
    allowed = settings.auth_api_keys
    if api_key and any(compare_digest(api_key, key) for key in allowed):
        request.state.tenant_id = "demo"
        request.state.api_key_id = _api_key_label(api_key)
        return "demo"
    if not settings.tenant_api_keys and not allowed and settings.is_local:
        request.state.tenant_id = "demo"
        return "demo"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key.",
        headers={"WWW-Authenticate": API_KEY_HEADER},
    )


async def get_current_tenant(
    tenant_id: Annotated[str, Depends(require_api_key)],
) -> str:
    return tenant_id


def _api_key_label(api_key: str) -> str:
    if len(api_key) <= 4:
        return "***"
    return f"{api_key[:2]}***{api_key[-2:]}"
