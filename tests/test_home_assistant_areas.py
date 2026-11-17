import json

import httpx
import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from httpx_ws.transport import ASGIWebSocketTransport

from src.tools import ToolRegistry
from src.tools.home_assistant import HomeAssistantClient


@pytest.fixture
def area_api() -> tuple[FastAPI, dict[str, object], list[str]]:
    app = FastAPI()
    requests: list[str] = []
    responses: dict[str, object] = {
        "config/area_registry/list": [
            {"area_id": "kitchen-area", "name": "Kitchen", "aliases": ["Cooking"]},
            {"area_id": "office-area", "name": "Office", "aliases": []},
            {"area_id": "unused-area", "name": "Unused", "aliases": []},
        ],
        "config/device_registry/list": [
            {"id": "kitchen-device", "area_id": "kitchen-area"},
            {"id": "office-device", "area_id": "office-area"},
        ],
        "config/entity_registry/list": [
            {
                "entity_id": entity_id,
                "area_id": area_id,
                "device_id": device_id,
                "labels": ["indicator"] if entity_id == "light.backlight" else [],
            }
            for entity_id, area_id, device_id in [
                ("light.ceiling", None, "kitchen-device"),
                ("light.override", "kitchen-area", "office-device"),
                ("light.kitchen_name_but_office", "office-area", "kitchen-device"),
                ("light.office_plasma", None, "office-device"),
                ("light.kitchen_name_but_unassigned", None, None),
                ("light.backlight", "kitchen-area", None),
            ]
        ],
        "config/label_registry/list": [
            {"label_id": "indicator", "name": "Indicator_Lighting"}
        ],
    }

    @app.websocket("/api/websocket")
    async def registry(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"type": "auth_required"})
        assert await websocket.receive_json() == {
            "type": "auth",
            "access_token": "test-token",
        }
        await websocket.send_json({"type": "auth_ok"})
        try:
            while True:
                command = await websocket.receive_json()
                requests.append(command["type"])
                result = responses[command["type"]]
                await websocket.send_json(
                    {
                        "type": "result",
                        "id": command["id"],
                        "success": result is not None,
                        "result": result,
                        "error": {"message": "private-error-details"},
                    }
                )
        except WebSocketDisconnect:
            return

    @app.get("/api/states")
    async def states() -> list[dict[str, object]]:
        requests.append("/api/states")
        return [
            {"entity_id": entity_id, "state": state, "attributes": {}}
            for entity_id, state in [
                ("light.ceiling", "on"),
                ("light.override", "off"),
                ("light.kitchen_name_but_office", "on"),
                ("light.office_plasma", "on"),
                ("light.kitchen_name_but_unassigned", "on"),
                ("light.backlight", "on"),
            ]
        ]

    return app, responses, requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("area", "include_indicators", "expected_ids"),
    [
        ("kitchen", False, ["light.ceiling", "light.override"]),
        ("kitchen-area", False, ["light.ceiling", "light.override"]),
        ("Cooking", False, ["light.ceiling", "light.override"]),
        ("Kitchen", True, ["light.ceiling", "light.override", "light.backlight"]),
        ("Office", False, ["light.kitchen_name_but_office", "light.office_plasma"]),
        ("Unused", False, []),
    ],
)
async def test_area_filter_uses_assignments_not_entity_names(
    area_api: tuple[FastAPI, dict[str, object], list[str]],
    area: str,
    include_indicators: bool,
    expected_ids: list[str],
) -> None:
    app, _, _ = area_api
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
                json.dumps({
                    "domain": "light",
                    "area": area,
                    "include_indicators": include_indicators,
                }),
            )
        )

    assert [entity["entity_id"] for entity in result] == expected_ids
    if "light.override" in expected_ids:
        assert next(
            entity["state"]
            for entity in result
            if entity["entity_id"] == "light.override"
        ) == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("missing", "area_not_found"),
        ("ambiguous", "area_ambiguous"),
        ("denied", "area_lookup_failed"),
        ("invalid_entities", "area_lookup_failed"),
    ],
)
async def test_area_errors_never_fall_back_to_whole_house(
    area_api: tuple[FastAPI, dict[str, object], list[str]],
    failure: str,
    expected_error: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, responses, requests = area_api
    if failure == "ambiguous":
        responses["config/area_registry/list"] = [
            {"area_id": "kitchen-area", "name": "Kitchen", "aliases": []},
            {"area_id": "other", "name": "Other", "aliases": ["Kitchen"]},
        ]
    elif failure == "denied":
        responses["config/area_registry/list"] = None
    elif failure == "invalid_entities":
        responses["config/entity_registry/list"] = [{"entity_id": "light.ceiling"}]

    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://ha.local"
    ) as http:
        tools = ToolRegistry(HomeAssistantClient("http://ha.local", "test-token", http))
        result = await tools.execute(
            "list_entities",
            json.dumps({
                "domain": "light",
                "area": "Missing" if failure == "missing" else "Kitchen",
            }),
        )

    assert json.loads(result) == {"error": expected_error}
    assert "/api/states" not in requests
    assert caplog.records
    assert "private-error-details" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("area", ["", "  ", 123])
async def test_invalid_area_is_rejected_without_querying_ha(area: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid areas must not make HTTP or WebSocket requests")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ha.local"
    ) as http:
        tools = ToolRegistry(HomeAssistantClient("http://ha.local", "unused", http))
        with pytest.raises(ValueError, match="area"):
            await tools.execute(
                "list_entities", json.dumps({"domain": "light", "area": area})
            )
