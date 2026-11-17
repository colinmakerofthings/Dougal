import json

import httpx
import pytest
from openai import AsyncOpenAI

from src.agent import DougalAgent
from src.tools import ToolRegistry
from src.tools.home_assistant import HomeAssistantClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "question", "ha_status", "ha_json", "expected", "answer"),
    [
        (
            "get_status",
            "{}",
            "Is Home Assistant running?",
            200,
            {"message": "API running."},
            {"status": "running"},
            "Ah sure, Home Assistant is answering. The API is running!",
        ),
        (
            "list_entities",
            '{"domain":"light"}',
            "Are any lights on?",
            200,
            [
                {
                    "entity_id": "light.kitchen",
                    "state": "on",
                    "attributes": {"friendly_name": "Kitchen"},
                },
                {"entity_id": "light.bedroom", "state": "unavailable", "attributes": {}},
            ],
            [
                {"entity_id": "light.kitchen", "state": "on", "name": "Kitchen"},
                {
                    "entity_id": "light.bedroom",
                    "state": "unavailable",
                    "name": "light.bedroom",
                },
            ],
            "The Kitchen light is on. I couldn't confirm the bedroom light's state.",
        ),
        (
            "list_entities",
            '{"domain":"light"}',
            "Are any lights on?",
            403,
            {"message": "private-response-details"},
            {"error": "authentication_failed", "http_status": 403},
            "Home Assistant denied access, so I couldn't check your lights.",
        ),
    ],
)
async def test_tool_result_is_returned_to_model_before_reply(
    tool_name: str,
    arguments: str,
    question: str,
    ha_status: int,
    ha_json: dict[str, object] | list[dict[str, object]],
    expected: dict[str, object] | list[dict[str, object]],
    answer: str,
) -> None:
    api_checks = []

    def ha_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        api_checks.append(request.url.path)
        return httpx.Response(ha_status, json=ha_json)

    def ai_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["messages"][-1]["role"] == "user":
            assert any(
                tool["function"]["name"] == tool_name for tool in payload["tools"]
            )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "status-check",
                        "type": "function",
                        "function": {"name": tool_name, "arguments": arguments},
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            result = payload["messages"][-1]
            assert result["role"] == "tool"
            assert result["tool_call_id"] == "status-check"
            assert json.loads(result["content"]) == expected
            message = {
                "role": "assistant",
                "content": answer,
            }
            finish_reason = "stop"
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {"index": 0, "message": message, "finish_reason": finish_reason}
                ],
            },
        )

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(ha_handler), base_url="http://ha.local"
        ) as ha_http,
        AsyncOpenAI(
            api_key="test-key",
            base_url="https://provider.test/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(ai_handler)),
        ) as ai,
    ):
        tools = ToolRegistry(HomeAssistantClient("http://ha.local", "unused", ha_http))
        agent = DougalAgent(ai, "test-model", tools)
        reply = await agent.respond(question)

    assert reply == answer
    assert api_checks == ["/api/" if tool_name == "get_status" else "/api/states"]
