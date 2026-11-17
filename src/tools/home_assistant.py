"""Small asynchronous Home Assistant REST API client."""

import logging
import re
from typing import Any
from urllib.parse import quote

import httpx


logger = logging.getLogger(__name__)
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

    async def get_status(self) -> dict[str, str | int]:
        try:
            response = await self._client.get("/api/")
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            logger.warning("Home Assistant status check returned HTTP %s", status_code)
            if status_code in (401, 403):
                return {"status": "authentication_failed", "http_status": status_code}
            return {"status": "http_error", "http_status": status_code}
        except httpx.RequestError:
            logger.warning("Home Assistant status check could not reach the API")
            return {"status": "unreachable"}
        return {"status": "running"}

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        if not _ENTITY_ID.fullmatch(entity_id):
            raise ValueError("Invalid Home Assistant entity ID")
        response = await self._client.get(f"/api/states/{quote(entity_id, safe='')}")
        response.raise_for_status()
        return response.json()

    async def list_entities(self, domain: str) -> list[dict[str, Any]]:
        if not _SERVICE_PART.fullmatch(domain):
            raise ValueError("Invalid Home Assistant domain")
        response = await self._client.get("/api/states")
        response.raise_for_status()
        return [
            {
                "entity_id": entity["entity_id"],
                "state": entity["state"],
                "name": entity["attributes"].get("friendly_name") or entity["entity_id"],
            }
            for entity in response.json()
            if entity["entity_id"].startswith(f"{domain}.")
        ]

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
