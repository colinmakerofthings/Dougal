from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from src.config import Settings


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
    assert 'href="/health"' in response.text
    assert 'href="/metrics"' in response.text
    assert "__UPTIME__" not in response.text
    assert "__HISTORY__" not in response.text
    assert "No messages yet." in response.text
    assert "private-" not in response.text
    assert "987654321" not in response.text
    assert app.state.http_requests == 1


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
async def test_history_keeps_latest_100_exchanges_and_resets_per_app(
    app: FastAPI, settings: Settings
) -> None:
    from src.main import create_app

    history = app.state.message_history
    history.add("discarded-message", "discarded-reply")
    for index in range(1, 101):
        history.add(f"user-message-{index}", f"bot-reply-{index}")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert response.text.count('<article class="exchange">') == 100
    assert "discarded-message" not in response.text
    assert "discarded-reply" not in response.text
    assert response.text.index(">user-message-100<") < response.text.index(
        ">user-message-1<"
    )
    assert "bot-reply-100" in response.text
    assert len(create_app(settings).state.message_history.exchanges) == 0


@pytest.mark.asyncio
async def test_history_escapes_user_and_assistant_html(app: FastAPI) -> None:
    app.state.message_history.add(
        '<script>alert("user")</script>\nSecond line',
        '<img src=x onerror="alert(1)"> & response',
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert '<script>' not in response.text
    assert '<img' not in response.text
    assert "&lt;script&gt;alert(&quot;user&quot;)&lt;/script&gt;\nSecond line" in response.text
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; response" in response.text
    assert "UTC</time>" in response.text
    assert response.headers["cache-control"] == "no-store"
