import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from httpx_ws.transport import ASGIWebSocketTransport

from src.tools import ToolRegistry
from src.tools.home_assistant import HomeAssistantClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (200, {"status": "running"}),
        (401, {"status": "authentication_failed", "http_status": 401}),
        (403, {"status": "authentication_failed", "http_status": 403}),
        (503, {"status": "http_error", "http_status": 503}),
    ],
)
async def test_status_tool_checks_configured_api(
    status_code: int,
    expected: dict[str, str | int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "http://ha.local/api/"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(status_code, text="private-response-details")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://ha.local",
        headers={"Authorization": "Bearer test-token"},
    ) as http:
        registry = ToolRegistry(HomeAssistantClient("http://ha.local", "unused", http))
        result = await registry.execute("get_status", "{}")

    assert json.loads(result) == expected
    assert "private-response-details" not in caplog.text
    if status_code != 200:
        assert caplog.records


@pytest.mark.asyncio
@pytest.mark.parametrize("error_class", [httpx.ConnectError, httpx.ReadTimeout])
async def test_status_tool_reports_unreachable_without_claiming_stopped(
    error_class: type[httpx.RequestError],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_class("private-connection-details", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ha.local"
    ) as http:
        client = HomeAssistantClient("http://ha.local", "unused", http)
        assert await client.get_status() == {"status": "unreachable"}

    assert caplog.records
    assert "private-connection-details" not in caplog.text


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


@pytest.mark.asyncio
@pytest.mark.parametrize("include_lights", [True, False])
async def test_list_entities_discovers_only_requested_domain(
    include_lights: bool,
) -> None:
    lights = [
        {
            "entity_id": "light.kitchen",
            "state": "on",
            "attributes": {"friendly_name": "Kitchen", "private_attribute": "unused"},
        },
        {"entity_id": "light.hall", "state": "off", "attributes": {}},
        {"entity_id": "light.bedroom", "state": "unavailable", "attributes": {}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/states"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json=[
                {"entity_id": "sensor.private", "state": "private-value", "attributes": {}},
                *(lights if include_lights else []),
            ],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://ha.local",
        headers={"Authorization": "Bearer test-token"},
    ) as http:
        registry = ToolRegistry(HomeAssistantClient("http://ha.local", "unused", http))
        result = json.loads(await registry.execute("list_entities", '{"domain":"light"}'))

    assert result == (
        [
            {"entity_id": "light.kitchen", "state": "on", "name": "Kitchen"},
            {"entity_id": "light.hall", "state": "off", "name": "light.hall"},
            {
                "entity_id": "light.bedroom",
                "state": "unavailable",
                "name": "light.bedroom",
            },
        ] if include_lights else []
    )


@pytest.mark.asyncio
async def test_list_entities_rejects_invalid_domain_without_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid domains must not make HTTP requests")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ha.local"
    ) as http:
        client = HomeAssistantClient("http://ha.local", "unused", http)
        with pytest.raises(ValueError, match="domain"):
            await client.list_entities("../../api")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "status_code", "error"),
    [
        ("get_state", '{"entity_id":"light.missing"}', 404, "not_found"),
        ("list_entities", '{"domain":"light"}', 401, "authentication_failed"),
        ("list_entities", '{"domain":"light"}', 503, "http_error"),
        (
            "call_service",
            '{"domain":"light","service":"turn_on","entity_id":"light.kitchen","service_data":null}',
            403,
            "authentication_failed",
        ),
    ],
)
async def test_tool_http_failures_return_safe_specific_errors(
    tool: str,
    arguments: str,
    status_code: int,
    error: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="private-error-details")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ha.local"
    ) as http:
        registry = ToolRegistry(HomeAssistantClient("http://ha.local", "unused", http))
        result = await registry.execute(tool, arguments)

    assert json.loads(result) == {"error": error, "http_status": status_code}
    assert caplog.records
    assert "private-error-details" not in caplog.text


@pytest.mark.asyncio
async def test_tool_connection_failure_reports_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-error-details", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ha.local"
    ) as http:
        registry = ToolRegistry(HomeAssistantClient("http://ha.local", "unused", http))
        result = await registry.execute("list_entities", '{"domain":"light"}')

    assert json.loads(result) == {"error": "unreachable"}
    assert caplog.records
    assert "private-error-details" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("include_indicators", [False, True])
async def test_indicator_filter_uses_exact_entity_label_and_allows_explicit_override(
    include_indicators: bool,
) -> None:
    app = FastAPI()
    registry_commands: list[str] = []
    entities = [
        {"entity_id": "light.kitchen", "labels": []},
        {"entity_id": "light.backlight", "labels": ["opaque-label-id"]},
        {"entity_id": "light.prefix", "labels": ["prefix-label-id"]},
        {"entity_id": "light.lowercase", "labels": ["lowercase-label-id"]},
        {"entity_id": "light.indicator_lighting_named_only", "labels": []},
        {"entity_id": "switch.kitchen", "labels": ["opaque-label-id"]},
    ]

    @app.websocket("/api/websocket")
    async def registry(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"type": "auth_required"})
        assert await websocket.receive_json() == {
            "type": "auth",
            "access_token": "test-token",
        }
        await websocket.send_json({"type": "auth_ok"})
        for expected_type, result in [
            (
                "config/label_registry/list",
                [
                    {
                        "label_id": "opaque-label-id",
                        "name": "Indicator_Lighting",
                        "description": "x" * 70000,
                    },
                    {"label_id": "prefix-label-id", "name": "Indicator_Lighting_Extra"},
                    {"label_id": "lowercase-label-id", "name": "indicator_lighting"},
                ],
            ),
            ("config/entity_registry/list", entities),
        ]:
            command = await websocket.receive_json()
            assert command["type"] == expected_type
            registry_commands.append(command["type"])
            await websocket.send_json(
                {"id": command["id"], "type": "result", "success": True, "result": result}
            )
        await websocket.receive()

    @app.get("/api/states")
    async def states() -> list[dict[str, object]]:
        return [
            {"entity_id": entity["entity_id"], "state": "on", "attributes": {}}
            for entity in entities
        ]

    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://ha.local"
    ) as http:
        tools = ToolRegistry(
            HomeAssistantClient(
                "http://ha.local",
                "test-token",
                http,
                indicator_label="Indicator_Lighting",
            )
        )
        result = json.loads(
            await tools.execute(
                "list_entities",
                json.dumps({"domain": "light", "include_indicators": include_indicators}),
            )
        )
        switches = json.loads(
            await tools.execute("list_entities", '{"domain":"switch"}')
        )

    assert [entity["entity_id"] for entity in result] == (
        [
            "light.kitchen",
            "light.backlight",
            "light.prefix",
            "light.lowercase",
            "light.indicator_lighting_named_only",
        ]
        if include_indicators
        else [
            "light.kitchen",
            "light.prefix",
            "light.lowercase",
            "light.indicator_lighting_named_only",
        ]
    )
    assert switches == [
        {"entity_id": "switch.kitchen", "state": "on", "name": "switch.kitchen"}
    ]
    assert registry_commands == (
        []
        if include_indicators
        else [
            "config/label_registry/list",
            "config/entity_registry/list",
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "authentication",
        "missing_label",
        "denied",
        "invalid_labels",
        "invalid_entities",
        "disconnect",
    ],
)
async def test_indicator_lookup_failure_is_explicit_and_never_returns_unfiltered_lights(
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    state_requests: list[str] = []

    @app.websocket("/api/websocket")
    async def registry(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"type": "auth_required"})
        await websocket.receive_json()
        if failure == "authentication":
            await websocket.send_json(
                {"type": "auth_invalid", "message": "private-error-details"}
            )
        elif failure == "disconnect":
            await websocket.close(code=1008, reason="private-error-details")
            return
        else:
            await websocket.send_json({"type": "auth_ok"})
            command = await websocket.receive_json()
            labels: object = [{"label_id": "actual-id", "name": "Indicator_Lighting"}]
            if failure == "missing_label":
                labels = [{"label_id": "other", "name": "Indicator_Lighting_Extra"}]
            elif failure == "invalid_labels":
                labels = [{"name": "Indicator_Lighting"}]
            await websocket.send_json(
                {
                    "id": command["id"],
                    "type": "result",
                    "success": failure != "denied",
                    "result": labels,
                    "error": {"message": "private-error-details"},
                }
            )
            if failure == "invalid_entities":
                command = await websocket.receive_json()
                await websocket.send_json(
                    {
                        "id": command["id"],
                        "type": "result",
                        "success": True,
                        "result": [
                            {"entity_id": "light.backlight", "labels": "not-a-list"}
                        ],
                    }
                )
        await websocket.receive()

    @app.get("/api/states")
    async def states() -> list[dict[str, object]]:
        state_requests.append("called")
        return []

    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://ha.local"
    ) as http:
        tools = ToolRegistry(
            HomeAssistantClient(
                "http://ha.local",
                "test-token",
                http,
                indicator_label="Indicator_Lighting",
            )
        )
        result = await tools.execute("list_entities", '{"domain":"light"}')

    assert json.loads(result) == {"error": "indicator_label_lookup_failed"}
    assert state_requests == []
    assert caplog.records
    assert "private-error-details" not in result
    assert "private-error-details" not in caplog.text


@pytest.mark.asyncio
async def test_indicator_lookup_timeout_is_reported_without_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()

    @app.websocket("/api/websocket")
    async def registry(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.receive()

    monkeypatch.setattr(
        "src.tools.home_assistant.asyncio",
        SimpleNamespace(timeout=lambda _: asyncio.timeout(0.05)),
    )
    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://ha.local"
    ) as http:
        tools = ToolRegistry(
            HomeAssistantClient(
                "http://ha.local",
                "test-token",
                http,
                indicator_label="Indicator_Lighting",
            )
        )
        result = await tools.execute("list_entities", '{"domain":"light"}')

    assert json.loads(result) == {"error": "indicator_label_lookup_failed"}
