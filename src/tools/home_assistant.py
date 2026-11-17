"""Small asynchronous Home Assistant REST API client."""

import re
from typing import Any
from urllib.parse import quote

import httpx


_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_SERVICE_PART = re.compile(r"^[a-z0-9_]+$")


class HomeAssistantClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(10.0),
        )
        self._owns_client = client is None

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        if not _ENTITY_ID.fullmatch(entity_id):
            raise ValueError("Invalid Home Assistant entity ID")
        response = await self._client.get(f"/api/states/{quote(entity_id, safe='')}")
        response.raise_for_status()
        return response.json()

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        service_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        if not _SERVICE_PART.fullmatch(domain) or not _SERVICE_PART.fullmatch(service):
            raise ValueError("Invalid Home Assistant service")
        payload: dict[str, Any] = dict(service_data or {})
        if entity_id is not None:
            if not _ENTITY_ID.fullmatch(entity_id):
                raise ValueError("Invalid Home Assistant entity ID")
            payload["entity_id"] = entity_id
        response = await self._client.post(
            f"/api/services/{quote(domain, safe='')}/{quote(service, safe='')}",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
