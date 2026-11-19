"""Registry of the agent's explicitly available tools."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.tools.home_assistant import (
    AreaLookupError,
    HomeAssistantClient,
    IndicatorLabelLookupError,
)

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]
logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self, home_assistant: HomeAssistantClient) -> None:
        self._handlers: dict[str, ToolHandler] = {
            "get_status": self._get_status,
            "get_state": self._get_state,
            "list_entities": self._list_entities,
            "call_service": self._call_service,
        }
        self.definitions = [
            {
                "type": "function",
                "function": {
                    "name": "get_status",
                    "description": (
                        "Check whether the configured Home Assistant API is running "
                        "and accessible. Use when asked if Home Assistant is running, "
                        "online, reachable, or connected. No entity ID is needed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_state",
                    "description": "Get the current state and attributes of a Home Assistant entity.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_id": {
                                "type": "string",
                                "description": "Entity ID, for example light.kitchen.",
                            }
                        },
                        "required": ["entity_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_entities",
                    "description": (
                        "Discover Home Assistant entities in a domain, with their "
                        "names, IDs, and current states. Use domain light to answer "
                        "questions such as 'Are any lights on?' without guessing "
                        "entity IDs. Includes unavailable and unknown entities. "
                        "General light discovery excludes the configured indicator "
                        "label. Set include_indicators true only for explicit "
                        "questions about indicators, backlights, or all light entities. "
                        "When a room is mentioned, supply area to filter using Home "
                        "Assistant's actual area assignments, not entity names."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "description": "Entity domain, for example light or switch.",
                            },
                            "include_indicators": {
                                "type": "boolean",
                                "description": (
                                    "Include indicator-labelled lights. Defaults to false."
                                ),
                            },
                            "area": {
                                "type": ["string", "null"],
                                "description": (
                                    "HA Area name, ID, or alias, such as Kitchen. "
                                    "Omit for a whole-house query."
                                ),
                            },
                            "display": {
                                "type": "boolean",
                                "description": (
                                    "Display a formatted entity list directly to the user. "
                                    "Use for list/summary answers, not discovery before actions."
                                ),
                            },
                            "display_state": {
                                "type": ["string", "null"],
                                "description": (
                                    "Only display this exact state. For example, use off "
                                    "for 'which lights are off?' and on for 'which lights "
                                    "are on?'; omit to group all states. Does not filter "
                                    "discovery data."
                                ),
                            },
                            "show_entity_ids": {
                                "type": "boolean",
                                "description": (
                                    "Show IDs in the formatted list only when explicitly "
                                    "requested for details/debugging. Defaults to false."
                                ),
                            },
                        },
                        "required": ["domain"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "call_service",
                    "description": (
                        "Call a Home Assistant service. Only do this when the user "
                        "clearly asks for an action."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "domain": {"type": "string"},
                            "service": {"type": "string"},
                            "entity_id": {"type": ["string", "null"]},
                            "service_data": {
                                "type": ["object", "null"],
                                "additionalProperties": True,
                            },
                        },
                        "required": ["domain", "service", "entity_id", "service_data"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
        self._home_assistant = home_assistant

    async def execute(self, name: str, arguments: str) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        try:
            result = await handler(json.loads(arguments))
        except IndicatorLabelLookupError:
            logger.warning("Home Assistant indicator label lookup failed")
            result = {"error": "indicator_label_lookup_failed"}
        except AreaLookupError as error:
            logger.warning("Home Assistant area lookup failed (%s)", error.code)
            result = {"error": error.code}
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            logger.warning("Home Assistant tool %s returned HTTP %s", name, status_code)
            if status_code in (401, 403):
                error_code = "authentication_failed"
            elif status_code == 404:
                error_code = "not_found"
            else:
                error_code = "http_error"
            result = {"error": error_code, "http_status": status_code}
        except httpx.RequestError:
            logger.warning("Home Assistant tool %s could not reach the API", name)
            result = {"error": "unreachable"}
        return json.dumps(result, default=str)

    async def _get_status(self, arguments: dict[str, Any]) -> Any:
        return await self._home_assistant.get_status()

    async def _get_state(self, arguments: dict[str, Any]) -> Any:
        return await self._home_assistant.get_state(arguments["entity_id"])

    async def _list_entities(self, arguments: dict[str, Any]) -> Any:
        for key in ("display", "show_entity_ids"):
            if key in arguments and not isinstance(arguments[key], bool):
                raise ValueError(f"{key} must be a boolean")
        state = arguments.get("display_state")
        if state is not None and (not isinstance(state, str) or not state.strip()):
            raise ValueError("display_state must be a non-empty string")
        return await self._home_assistant.list_entities(
            arguments["domain"],
            include_indicators=arguments.get("include_indicators", False),
            area=arguments.get("area"),
        )

    async def _call_service(self, arguments: dict[str, Any]) -> Any:
        return await self._home_assistant.call_service(
            domain=arguments["domain"],
            service=arguments["service"],
            entity_id=arguments["entity_id"],
            service_data=arguments["service_data"],
        )
