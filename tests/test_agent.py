import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import AsyncOpenAI

from src.agent import DougalAgent
from src.entity_responses import EntityResponse
from src.tools import ToolRegistry
from src.tools.home_assistant import HomeAssistantClient


@pytest.mark.asyncio
async def test_agent_can_execute_an_explicit_action() -> None:
    home_assistant = MagicMock(spec=HomeAssistantClient)
    home_assistant.call_service = AsyncMock(return_value=[])
    tools = ToolRegistry(home_assistant)
    client = MagicMock(spec=AsyncOpenAI)
    completion = client.chat.completions.create = AsyncMock()
    tool_call = MagicMock()
    tool_call.id = "service-call"
    tool_call.function.name = "call_service"
    tool_call.function.arguments = json.dumps(
        {
            "domain": "light",
            "service": "turn_off",
            "entity_id": "light.kitchen",
            "service_data": None,
        }
    )
    tool_message = MagicMock()
    tool_message.tool_calls = [tool_call]
    tool_message.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "service-call",
                "type": "function",
                "function": {
                    "name": "call_service",
                    "arguments": tool_call.function.arguments,
                },
            }
        ],
    }
    final_message = MagicMock()
    final_message.tool_calls = None
    final_message.content = "The kitchen light is off."
    completion.side_effect = [
        MagicMock(choices=[MagicMock(message=tool_message)]),
        MagicMock(choices=[MagicMock(message=final_message)]),
    ]

    reply = await DougalAgent(client, "test-model", tools).respond(
        "Turn off the kitchen lights"
    )

    definitions = completion.call_args_list[0].kwargs["tools"]
    assert "call_service" in {tool["function"]["name"] for tool in definitions}
    assert reply == "The kitchen light is off."
    home_assistant.call_service.assert_awaited_once_with(
        domain="light",
        service="turn_off",
        entity_id="light.kitchen",
        service_data=None,
    )


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
            "The Kitchen light is on.",
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
        (
            "list_entities",
            '{"domain":"light","display":true}',
            "List my lights",
            403,
            {"message": "private-response-details"},
            {"error": "authentication_failed", "http_status": 403},
            "Home Assistant denied access, so I couldn't list your lights.",
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


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_displayed_lists_use_tool_data_without_model_rewriting(empty: bool) -> None:
    entities = [] if empty else [
        {"entity_id": "light.kitchen", "name": "Kitchen", "state": "on"}
    ]
    home_assistant = MagicMock(spec=HomeAssistantClient)
    home_assistant.list_entities = AsyncMock(return_value=entities)
    requests = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [{
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "This model-written list must not be used.",
                        "tool_calls": [{
                            "id": "list",
                            "type": "function",
                            "function": {
                                "name": "list_entities",
                                "arguments": json.dumps({
                                    "domain": "light",
                                    "area": "Kitchen",
                                    "display": True,
                                    "display_state": "on",
                                    "show_entity_ids": True,
                                    "include_indicators": True,
                                }),
                            },
                        }],
                    },
                }],
            },
        )

    async with AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(ai_handler)),
    ) as ai:
        response = await DougalAgent(ai, "test-model", ToolRegistry(home_assistant)).respond(
            "List the on kitchen lights, including indicators, with IDs"
        )

    assert isinstance(response, EntityResponse)
    assert response.text == ""
    assert len(response.lists) == 1
    report = response.lists[0]
    assert report.entities == entities
    assert report.domain == "light"
    assert report.area == "Kitchen"
    assert report.state == "on"
    assert report.show_entity_ids
    assert len(requests) == 1
    home_assistant.list_entities.assert_awaited_once_with(
        "light", area="Kitchen", include_indicators=True
    )
