import httpx
import pytest

from src.tools.home_assistant import HomeAssistantClient


@pytest.mark.asyncio
async def test_get_state_uses_bearer_auth_and_returns_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/states/light.kitchen"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"entity_id": "light.kitchen", "state": "on"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        transport=transport,
        base_url="http://ha.local",
        headers={"Authorization": "Bearer test-token"},
    )
    client = HomeAssistantClient("http://ha.local", "unused", client=http)

    assert await client.get_state("light.kitchen") == {
        "entity_id": "light.kitchen",
        "state": "on",
    }
    await client.close()
    await http.aclose()


@pytest.mark.asyncio
async def test_call_service_posts_entity_and_service_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/services/light/turn_on"
        assert request.read() == b'{"brightness":128,"entity_id":"light.kitchen"}'
        return httpx.Response(200, json=[{"entity_id": "light.kitchen"}])

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://ha.local",
    )
    client = HomeAssistantClient("http://ha.local", "unused", client=http)

    result = await client.call_service(
        "light", "turn_on", "light.kitchen", {"brightness": 128}
    )

    assert result == [{"entity_id": "light.kitchen"}]
    await http.aclose()


@pytest.mark.asyncio
async def test_rejects_malformed_entity_id_without_request() -> None:
    http = httpx.AsyncClient(base_url="http://ha.local")
    client = HomeAssistantClient("http://ha.local", "unused", client=http)

    with pytest.raises(ValueError, match="entity ID"):
        await client.get_state("../../secrets")
    await http.aclose()
