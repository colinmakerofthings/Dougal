import json

import httpx
import pytest
from openai import AsyncOpenAI

from src.agent import DougalAgent
from src.tools import ToolRegistry
from src.tools.home_assistant import HomeAssistantClient


@pytest.mark.asyncio
async def test_status_tool_result_is_returned_to_model_before_reply() -> None:
    api_checks = []

    def ha_handler(request: httpx.Request) -> httpx.Response:
        api_checks.append(request.url.path)
        return httpx.Response(200, json={"message": "API running."})

    def ai_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["messages"][-1]["role"] == "user":
            assert any(
                tool["function"]["name"] == "get_status" for tool in payload["tools"]
            )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "status-check",
                        "type": "function",
                        "function": {"name": "get_status", "arguments": "{}"},
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            result = payload["messages"][-1]
            assert result["role"] == "tool"
            assert result["tool_call_id"] == "status-check"
            assert json.loads(result["content"]) == {"status": "running"}
            message = {
                "role": "assistant",
                "content": "Ah sure, Home Assistant is answering. The API is running!",
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
        reply = await agent.respond("Is Home Assistant running?")

    assert reply == "Ah sure, Home Assistant is answering. The API is running!"
    assert api_checks == ["/api/"]
