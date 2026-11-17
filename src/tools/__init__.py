"""Registry of the agent's explicitly available tools."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from src.tools.home_assistant import HomeAssistantClient

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class ToolRegistry:
    def __init__(self, home_assistant: HomeAssistantClient) -> None:
        self._handlers: dict[str, ToolHandler] = {
            "get_state": self._get_state,
            "call_service": self._call_service,
        }
        self.definitions = [
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
        result = await handler(json.loads(arguments))
        return json.dumps(result, default=str)

    async def _get_state(self, arguments: dict[str, Any]) -> Any:
        return await self._home_assistant.get_state(arguments["entity_id"])

    async def _call_service(self, arguments: dict[str, Any]) -> Any:
        return await self._home_assistant.call_service(
            domain=arguments["domain"],
            service=arguments["service"],
            entity_id=arguments["entity_id"],
            service_data=arguments["service_data"],
        )
