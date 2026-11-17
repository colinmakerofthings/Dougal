"""FastAPI service entrypoint and managed Telegram polling lifecycle."""

import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from openai import AsyncOpenAI

from src.agent import DougalAgent
from src.config import Settings
from src.telegram_bot import build_telegram_application
from src.tools import ToolRegistry
from src.tools.home_assistant import HomeAssistantClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings()
    started_at = time.monotonic()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        openai_options: dict[str, str] = {
            "api_key": configured.openai_api_key.get_secret_value()
        }
        if configured.openai_base_url:
            openai_options["base_url"] = configured.openai_base_url
        openai_client = AsyncOpenAI(**openai_options)
        ha_client = HomeAssistantClient(
            configured.home_assistant_url,
            configured.home_assistant_token.get_secret_value(),
        )
        agent = DougalAgent(
            openai_client,
            configured.openai_model,
            ToolRegistry(ha_client),
        )
        telegram = build_telegram_application(configured, agent)
        telegram_initialized = False
        try:
            await telegram.initialize()
            telegram_initialized = True
            if telegram.updater is None:
                raise RuntimeError("Telegram polling updater is unavailable")
            await telegram.updater.start_polling()
            await telegram.start()
            app.state.telegram = telegram
            yield
        finally:
            if telegram.updater is not None and telegram.updater.running:
                await telegram.updater.stop()
            if telegram.running:
                await telegram.stop()
            if telegram_initialized:
                await telegram.shutdown()
            await ha_client.close()
            await openai_client.close()

    app = FastAPI(title="Dougal", version="0.1.0", lifespan=lifespan)
    app.state.http_requests = 0

    @app.middleware("http")
    async def count_requests(request: Request, call_next):
        app.state.http_requests += 1
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        return (
            "# HELP dougal_http_requests_total Total HTTP requests.\n"
            "# TYPE dougal_http_requests_total counter\n"
            f"dougal_http_requests_total {app.state.http_requests}\n"
            "# HELP dougal_uptime_seconds Process uptime in seconds.\n"
            "# TYPE dougal_uptime_seconds gauge\n"
            f"dougal_uptime_seconds {time.monotonic() - started_at:.2f}\n"
        )

    return app


app = create_app()
