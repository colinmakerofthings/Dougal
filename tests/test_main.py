from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from src.config import Settings
from src.tools.home_assistant import HomeAssistantClient


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token="123456:private-bot-token",
        allowed_telegram_user_ids={987654321},
        openai_api_key="private-openai-key",
        openai_base_url="https://private-provider.test/v1",
        openai_model="private-model",
        home_assistant_url="http://private-ha.local",
        home_assistant_token="private-ha-token",
    )


@pytest.fixture
def app(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    with patch("src.config.Settings", return_value=settings):
        from src.main import create_app

    monkeypatch.setattr("src.main.time", SimpleNamespace(monotonic=lambda: 100.0))
    return create_app(settings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(0, "0d 0h 0m 0s"), (90061, "1d 1h 1m 1s")],
)
async def test_landing_page_is_read_only_overview_with_uptime(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    elapsed: int,
    expected: str,
) -> None:
    monkeypatch.setattr(
        "src.main.time", SimpleNamespace(monotonic=lambda: 100.0 + elapsed)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert expected in response.text
    assert f'data-seconds="{elapsed}"' in response.text
    assert "Since this service instance started. Updates automatically." not in response.text
    assert 'href="/health"' not in response.text
    assert 'href="/metrics"' not in response.text
    assert "__UPTIME__" not in response.text
    assert "__UPTIME_SECONDS__" not in response.text
    assert "__HISTORY__" not in response.text
    assert "No messages yet." in response.text
    assert "private-" not in response.text
    assert "987654321" not in response.text
    assert app.state.http_requests == 1


@pytest.mark.asyncio
async def test_landing_page_displays_the_supplied_picture(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/")
        picture = await client.get("/dougal.jpeg")

    assert 'src="/dougal.jpeg"' in page.text
    assert 'alt="Dougal smiling"' in page.text
    assert picture.status_code == 200
    assert picture.headers["content-type"] == "image/jpeg"
    assert picture.content.startswith(b"\xff\xd8\xff")


@pytest.mark.asyncio
async def test_uptime_endpoint_reports_current_uncached_service_uptime(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/api/uptime")
        monkeypatch.setattr(
            "src.main.time", SimpleNamespace(monotonic=lambda: 90161.9)
        )
        second = await client.get("/api/uptime")

    assert first.json() == {"uptime_seconds": 0}
    assert second.json() == {"uptime_seconds": 90061}
    assert first.headers["cache-control"] == "no-store"
    assert second.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_existing_health_and_metrics_are_unchanged(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        health = await client.get("/health")
        metrics = await client.get("/metrics")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")
    assert "dougal_http_requests_total 2\n" in metrics.text
    assert "dougal_uptime_seconds 0.00\n" in metrics.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ha_status", "expected"),
    [
        (200, {"status": "running"}),
        (401, {"status": "authentication_failed", "http_status": 401}),
        (403, {"status": "authentication_failed", "http_status": 403}),
        (503, {"status": "http_error", "http_status": 503}),
        (None, {"status": "unreachable"}),
    ],
)
async def test_home_assistant_status_reports_api_connection(
    app: FastAPI, ha_status: int | None, expected: dict[str, str | int]
) -> None:
    requests: list[httpx.Request] = []

    def handle_home_assistant(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if ha_status is None:
            raise httpx.ConnectError("private connection details", request=request)
        return httpx.Response(ha_status, json={"message": "private provider details"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle_home_assistant),
        base_url="http://ha.test",
    ) as ha_http:
        ha_client = HomeAssistantClient("http://ha.test", "test-token", ha_http)
        with patch("src.main.HomeAssistantClient", return_value=ha_client):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/home-assistant-status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == expected
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/"
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_status_reports_last_received_time_and_counts_without_message_details(
    app: FastAPI, settings: Settings
) -> None:
    from src.main import create_app

    history = app.state.message_history
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        initial = await client.get("/api/status")
        assert initial.status_code == 200
        assert initial.json() == {
            "last_message_received": None,
            "text_messages": 0,
            "voice_messages": 0,
        }

        received_at = datetime(2026, 11, 17, 16, 40, 0, tzinfo=UTC)
        with patch("src.history.datetime") as clock:
            clock.now.return_value = received_at
            history.record_received()

        response = await client.get("/api/status")
        assert response.status_code == 200
        assert response.json() == {
            "last_message_received": "2026-11-17T16:40:00+00:00",
            "text_messages": 0,
            "voice_messages": 0,
        }
        assert response.headers["cache-control"] == "no-store"
        assert history.exchanges == ()

        history.add("private-message", "private-reply", user_name="private-name")
        history.add("private-transcript", "private-reply", is_voice=True)
        history.add("another private message", "private-reply")
        after_reply = await client.get("/api/status")
        assert after_reply.json() == {
            "last_message_received": "2026-11-17T16:40:00+00:00",
            "text_messages": 2,
            "voice_messages": 1,
        }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        restarted = await client.get("/api/status")
    assert restarted.json() == {
        "last_message_received": None,
        "text_messages": 0,
        "voice_messages": 0,
    }


@pytest.mark.asyncio
async def test_history_labels_voice_transcripts(app: FastAPI) -> None:
    app.state.message_history.add("Which lights are off?", "Kitchen", is_voice=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert '<span class="source">via Telegram voice</span>' in response.text
    assert "Which lights are off?" in response.text


@pytest.mark.asyncio
async def test_history_keeps_latest_100_exchanges_and_resets_per_app(
    app: FastAPI, settings: Settings
) -> None:
    from src.main import create_app

    history = app.state.message_history
    history.add("discarded-message", "discarded-reply", is_voice=True)
    for index in range(1, 101):
        history.add(
            f"user-message-{index}", f"bot-reply-{index}", user_name="Colin Brown"
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
        status = await client.get("/api/status")

    assert status.json()["text_messages"] == 100
    assert status.json()["voice_messages"] == 0
    assert response.text.count('<article class="exchange">') == 100
    assert response.text.count('<span class="source">via Telegram</span>') == 100
    assert "discarded-message" not in response.text
    assert "discarded-reply" not in response.text
    assert response.text.index(">user-message-100<") < response.text.index(
        ">user-message-1<"
    )
    assert "bot-reply-100" in response.text
    assert '<p class="speaker">Colin Brown</p>' in response.text
    assert len(create_app(settings).state.message_history.exchanges) == 0


@pytest.mark.asyncio
async def test_history_escapes_name_user_and_assistant_html(app: FastAPI) -> None:
    app.state.message_history.add(
        '<script>alert("user")</script>\nSecond line',
        '<img src=x onerror="alert(1)"> & response',
        user_name='<b>Colin & "Brown"</b>',
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert '<script>alert("user")</script>' not in response.text
    assert '<img src=x onerror="alert(1)">' not in response.text
    assert (
        '<p class="speaker">&lt;b&gt;Colin &amp; &quot;Brown&quot;&lt;/b&gt;</p>'
        in response.text
    )
    assert "&lt;script&gt;alert(&quot;user&quot;)&lt;/script&gt;\nSecond line" in response.text
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; response" in response.text
    assert "UTC</time>" in response.text
    assert response.headers["cache-control"] == "no-store"
