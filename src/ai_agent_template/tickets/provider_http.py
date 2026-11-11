"""Shared HTTP transport for local ticket provider simulators."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import SecretStr


class ProviderRequestError(RuntimeError):
    """Safe provider failure category; contains no request body or credentials."""

    def __init__(self, code: str) -> None:
        super().__init__("Ticket provider request failed.")
        self.code = code


class MockProviderHttpClient:
    def __init__(
        self,
        base_url: str,
        token: SecretStr | None,
        timeout_seconds: float,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ticket provider URL must be HTTP or HTTPS.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        *,
        tenant_id: str,
        body: dict[str, object],
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        if self.token is None or not self.token.get_secret_value():
            raise ProviderRequestError("missing_credentials")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token.get_secret_value()}",
            "X-Tenant-ID": tenant_id,
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(  # noqa: S310 - URL scheme is validated in the constructor.
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise ProviderRequestError(f"provider_http_{error.code}") from error
        except (URLError, TimeoutError) as error:
            raise ProviderRequestError("network_error") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderRequestError("invalid_response") from error
        if not isinstance(result, dict):
            raise ProviderRequestError("invalid_response")
        return result
