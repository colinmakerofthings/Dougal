"""Small asynchronous Home Assistant REST API client."""

import asyncio
import logging
import re
from typing import Any, TypedDict
from urllib.parse import quote

import httpx
from httpx_ws import AsyncWebSocketSession, HTTPXWSException, aconnect_ws
from pydantic import TypeAdapter


logger = logging.getLogger(__name__)
_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_SERVICE_PART = re.compile(r"^[a-z0-9_]+$")


class _LabelEntry(TypedDict):
    label_id: str
    name: str


class _RegistryEntity(TypedDict):
    entity_id: str
    labels: list[str]


_LABEL_ENTRIES = TypeAdapter(list[_LabelEntry])
_REGISTRY_ENTITIES = TypeAdapter(list[_RegistryEntity])


class IndicatorLabelLookupError(Exception):
    """The configured indicator label could not be resolved safely."""


class HomeAssistantClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        client: httpx.AsyncClient | None = None,
        *,
        indicator_label: str = "",
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(10.0),
        )
        self._owns_client = client is None
        self._token = token
        self._indicator_label = indicator_label

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

    async def list_entities(
        self, domain: str, *, include_indicators: bool = False
    ) -> list[dict[str, Any]]:
        if not _SERVICE_PART.fullmatch(domain):
            raise ValueError("Invalid Home Assistant domain")
        if not isinstance(include_indicators, bool):
            raise ValueError("include_indicators must be a boolean")
        excluded: set[str] = set()
        if domain == "light" and self._indicator_label and not include_indicators:
            excluded = await self._indicator_entity_ids()
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
            and entity["entity_id"] not in excluded
        ]

    async def _indicator_entity_ids(self) -> set[str]:
        try:
            async with asyncio.timeout(10):
                async with aconnect_ws(
                    "/api/websocket",
                    self._client,
                    max_message_size_bytes=8 * 1024 * 1024,
                    keepalive_ping_interval_seconds=None,
                ) as websocket:
                    greeting = await websocket.receive_json()
                    if (
                        not isinstance(greeting, dict)
                        or greeting.get("type") != "auth_required"
                    ):
                        raise ValueError("Unexpected registry authentication handshake")
                    await websocket.send_json(
                        {"type": "auth", "access_token": self._token}
                    )
                    authenticated = await websocket.receive_json()
                    if (
                        not isinstance(authenticated, dict)
                        or authenticated.get("type") != "auth_ok"
                    ):
                        raise ValueError("Home Assistant registry authentication failed")
                    await websocket.send_json(
                        {"id": 1, "type": "config/label_registry/list"}
                    )
                    labels = _LABEL_ENTRIES.validate_python(
                        await self._registry_result(websocket, 1)
                    )
                    label_id = next(
                        (
                            label["label_id"]
                            for label in labels
                            if label["name"] == self._indicator_label
                        ),
                        None,
                    )
                    if label_id is None:
                        raise ValueError("Configured indicator label was not found")
                    await websocket.send_json(
                        {"id": 2, "type": "config/entity_registry/list"}
                    )
                    entities = _REGISTRY_ENTITIES.validate_python(
                        await self._registry_result(websocket, 2)
                    )
                    return {
                        entity["entity_id"]
                        for entity in entities
                        if label_id in entity["labels"]
                    }
        except* (HTTPXWSException, httpx.HTTPError, TimeoutError, ValueError):
            raise IndicatorLabelLookupError(
                "Unable to resolve the configured indicator label"
            ) from None

    @staticmethod
    async def _registry_result(
        websocket: AsyncWebSocketSession, command_id: int
    ) -> object:
        message = await websocket.receive_json()
        if (
            not isinstance(message, dict)
            or message.get("type") != "result"
            or message.get("id") != command_id
            or message.get("success") is not True
            or "result" not in message
        ):
            raise ValueError("Home Assistant registry lookup failed")
        return message["result"]

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
