import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import RateLimitError
from pydantic import HttpUrl
from telegram import Update, User, Voice
from telegram.constants import ChatType, ParseMode

from src.agent import DougalAgent
from src.config import Settings
from src.entity_responses import EntityList, EntityResponse
from src.history import MessageHistory
from src.telegram_bot import build_telegram_application


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "user_id", "chat_type", "duration", "size", "status", "reply", "api_key"),
    [
        (True, 123, ChatType.PRIVATE, 30, 20, 200, "The kitchen light is off.", ""),
        (True, 123, ChatType.PRIVATE, 30, 20, 200, "The kitchen light is off.", "scribe-key"),
        (True, 123, ChatType.PRIVATE, 30, 20, 401, "I couldn't transcribe", "rejected-key"),
        (True, 123, ChatType.PRIVATE, 30, 20, 503, "I couldn't transcribe", ""),
        (True, 123, ChatType.PRIVATE, 31, 20, 200, "up to 30 seconds", ""),
        (True, 123, ChatType.PRIVATE, 30, 2 * 1024 * 1024 + 1, 200, "2 MiB", ""),
        (True, 999, ChatType.PRIVATE, 30, 20, 200, None, ""),
        (True, 123, ChatType.GROUP, 30, 20, 200, None, ""),
        (False, 123, ChatType.PRIVATE, 30, 20, 200, None, ""),
    ],
)
async def test_voice_notes_use_local_transcription_and_agent(
    enabled: bool, user_id: int, chat_type: str, duration: int, size: int,
    status: int, reply: str | None, api_key: str, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    url = "http://transcriber.test/v1/audio/transcriptions"
    monkeypatch.setenv("DOUGAL_VOICE_TRANSCRIPTION_API_KEY", api_key)
    settings = Settings(
        telegram_bot_token="123456:test-token",
        allowed_telegram_user_ids={123},
        openai_api_key="test-key",
        home_assistant_url="http://ha.local",
        home_assistant_token="test-token",
        dougal_voice_transcription_url=HttpUrl(url) if enabled else None,
    )
    agent = MagicMock(spec=DougalAgent)
    agent.respond = AsyncMock(return_value="The kitchen light is off.")
    history = MessageHistory(tmp_path / "history.sqlite3")
    app = build_telegram_application(settings, agent, history=history)
    update = MagicMock(spec=Update)
    update.effective_user = User(id=user_id, first_name="Colin", is_bot=False)
    update.effective_chat.type = chat_type
    update.effective_message.text = None
    update.effective_message.voice = Voice("file", "unique", duration, file_size=size)
    update.effective_message.reply_text = AsyncMock()
    file = MagicMock(file_size=20)
    file.download_as_bytearray = AsyncMock(return_value=bytearray(b"OggS test audio"))
    requests = []

    def transcribe(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == url
        assert b"OggS test audio" in request.content
        assert b"whisper-1" in request.content
        assert request.headers.get("authorization") == (
            f"Bearer {api_key}" if api_key else None
        )
        return httpx.Response(status, json={"text": "  Turn off the kitchen lights  "})

    async_client = httpx.AsyncClient
    with (
        patch.object(Voice, "get_file", new=AsyncMock(return_value=file)) as get_file,
        patch(
            "src.telegram_bot.httpx.AsyncClient",
            side_effect=lambda **kwargs: async_client(
                transport=httpx.MockTransport(transcribe), **kwargs
            ),
        ),
    ):
        await app.handlers[0][0].callback(update, MagicMock())

    accepted = (
        enabled and user_id == 123 and chat_type == ChatType.PRIVATE
        and duration == 30 and size == 20
    )
    assert get_file.await_count == len(requests) == int(accepted)
    if accepted and status == 200:
        agent.respond.assert_awaited_once_with("Turn off the kitchen lights")
        [exchange] = await history.get_exchanges()
        assert exchange.user_message == "Turn off the kitchen lights"
        assert exchange.is_voice
    else:
        agent.respond.assert_not_awaited()
        assert await history.get_exchanges() == ()
    if reply is None:
        update.effective_message.reply_text.assert_not_awaited()
    else:
        assert reply in update.effective_message.reply_text.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error_type", "expected_reply"),
    [
        ("credit_balance_exhausted", "insufficient_quota", "run out of API credits"),
        ("insufficient_quota", "insufficient_quota", "run out of API credits"),
        (None, "insufficient_quota", "run out of API credits"),
        ("rate_limit_exceeded", "requests", "try again in a little while"),
    ],
)
async def test_provider_limits_get_safe_specific_replies(
    code: str | None,
    error_type: str,
    expected_reply: str,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        allowed_telegram_user_ids={123},
        openai_api_key="test-key",
        home_assistant_url="http://ha.local",
        home_assistant_token="test-token",
    )
    agent = MagicMock(spec=DougalAgent)
    agent.respond = AsyncMock(
        side_effect=RateLimitError(
            "private-provider-details",
            response=httpx.Response(
                429, request=httpx.Request("POST", "https://provider.test/v1")
            ),
            body={
                "code": code,
                "type": error_type,
                "message": "private-provider-details",
            },
        )
    )
    history = MessageHistory(tmp_path / "history.sqlite3")
    application = build_telegram_application(settings, agent, history=history)
    update = MagicMock(spec=Update)
    update.effective_user = User(
        id=123, first_name="Colin", last_name="Brown", is_bot=False
    )
    update.effective_chat.type = ChatType.PRIVATE
    update.effective_message.text = "Hello"
    update.effective_message.reply_text = AsyncMock()

    with caplog.at_level(logging.WARNING):
        await application.handlers[0][0].callback(update, MagicMock())

    agent.respond.assert_awaited_once_with("Hello")
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.call_args.args[0]
    assert expected_reply in reply
    assert "private-provider-details" not in reply
    assert "private-provider-details" not in caplog.text
    assert caplog.records
    assert all(record.exc_info is None for record in caplog.records)
    [exchange] = await history.get_exchanges()
    assert exchange.reply == reply
    assert "private-provider-details" not in exchange.reply


@pytest.mark.asyncio
@pytest.mark.parametrize("last_name", [None, "Brown"])
@pytest.mark.parametrize(
    ("user_id", "chat_type", "should_record", "message_text"),
    [
        (123, ChatType.PRIVATE, True, "Hello"),
        (999, ChatType.PRIVATE, False, "Hello"),
        (123, ChatType.GROUP, False, "Hello"),
        (123, ChatType.PRIVATE, False, None),
    ],
)
async def test_only_authorized_private_exchanges_are_recorded(
    user_id: int,
    chat_type: str,
    should_record: bool,
    message_text: str | None,
    last_name: str | None,
    tmp_path: Path,
) -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        allowed_telegram_user_ids={123},
        openai_api_key="test-key",
        home_assistant_url="http://ha.local",
        home_assistant_token="test-token",
    )
    history = MessageHistory(tmp_path / "history.sqlite3")
    agent = MagicMock(spec=DougalAgent)
    answer = "A" * 4100
    received_at = datetime(2026, 11, 17, 16, 40, 0, tzinfo=UTC)

    async def respond(text: str) -> str:
        assert (await history.get_status()).last_message_received_at == received_at
        assert await history.get_exchanges() == ()
        return answer

    agent.respond = AsyncMock(side_effect=respond)
    application = build_telegram_application(settings, agent, history=history)
    update = MagicMock(spec=Update)
    update.effective_user = User(
        id=user_id, first_name="Colin", last_name=last_name, is_bot=False
    )
    update.effective_chat.type = chat_type
    update.effective_message.text = message_text
    update.effective_message.reply_text = AsyncMock()

    with patch("src.history._utcnow", return_value=received_at):
        await application.handlers[0][0].callback(update, MagicMock())

    if should_record:
        [exchange] = await history.get_exchanges()
        assert exchange.user_message == "Hello"
        assert exchange.reply == answer
        assert exchange.user_name == (
            "Colin Brown" if last_name else "Colin"
        )
        assert update.effective_message.reply_text.await_count == 2
        assert "".join(
            call.args[0] for call in update.effective_message.reply_text.await_args_list
        ) == answer
    else:
        assert await history.get_exchanges() == ()
        assert (await history.get_status()).last_message_received_at is None
        agent.respond.assert_not_awaited()
        update.effective_message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_error_does_not_claim_home_assistant_is_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        allowed_telegram_user_ids={123},
        openai_api_key="test-key",
        home_assistant_url="http://ha.local",
        home_assistant_token="test-token",
    )
    agent = MagicMock(spec=DougalAgent)
    agent.respond = AsyncMock(side_effect=RuntimeError("private-error-details"))
    application = build_telegram_application(settings, agent)
    update = MagicMock(spec=Update)
    update.effective_user = User(id=123, first_name="Colin", is_bot=False)
    update.effective_chat.type = ChatType.PRIVATE
    update.effective_message.text = "Are any lights on?"
    update.effective_message.reply_text = AsyncMock()

    await application.handlers[0][0].callback(update, MagicMock())

    reply = update.effective_message.reply_text.call_args.args[0]
    assert "couldn't complete the request" in reply
    assert "couldn't reach" not in reply
    assert "private-error-details" not in reply
    assert "private-error-details" not in caplog.text
    assert caplog.records
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["record_received", "add"])
async def test_history_storage_failure_is_reported_without_private_details(
    tmp_path: Path, failure_point: str, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        allowed_telegram_user_ids={123},
        openai_api_key="test-key",
        home_assistant_url="http://ha.local",
        home_assistant_token="test-token",
    )
    agent = MagicMock(spec=DougalAgent)
    agent.respond = AsyncMock(return_value="Done.")
    history = MessageHistory(tmp_path / "history.sqlite3")
    application = build_telegram_application(settings, agent, history=history)
    update = MagicMock(spec=Update)
    update.effective_user = User(id=123, first_name="Colin", is_bot=False)
    update.effective_chat.type = ChatType.PRIVATE
    update.effective_message.text = "Hello"
    update.effective_message.reply_text = AsyncMock()

    with patch.object(
        history, failure_point,
        new=AsyncMock(side_effect=sqlite3.OperationalError("private database details")),
    ):
        await application.handlers[0][0].callback(update, MagicMock())

    replies = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
    if failure_point == "record_received":
        agent.respond.assert_not_awaited()
        assert replies == [
            "I couldn't record that message, so I couldn't process it. Please try again."
        ]
    else:
        agent.respond.assert_awaited_once()
        assert replies == ["Done.", "I couldn't save this exchange to history."]
    assert "private database details" not in caplog.text


@pytest.mark.asyncio
async def test_entity_replies_use_html_but_prose_and_history_remain_plain(
    tmp_path: Path,
) -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        allowed_telegram_user_ids={123},
        openai_api_key="test-key",
        home_assistant_url="http://ha.local",
        home_assistant_token="test-token",
    )
    agent = MagicMock(spec=DougalAgent)
    agent.respond = AsyncMock(return_value=EntityResponse(
        lists=[EntityList("light", [
            {"entity_id": "light.desk", "name": "<Desk & lamp>", "state": "on"},
        ])],
        text="An unrelated result: <not HTML>.",
    ))
    history = MessageHistory(tmp_path / "history.sqlite3")
    application = build_telegram_application(settings, agent, history=history)
    update = MagicMock(spec=Update)
    update.effective_user = User(id=123, first_name="Colin", is_bot=False)
    update.effective_chat.type = ChatType.PRIVATE
    update.effective_message.text = "List my lights"
    update.effective_message.reply_text = AsyncMock()

    await application.handlers[0][0].callback(update, MagicMock())

    prose, entity_list = update.effective_message.reply_text.await_args_list
    assert prose.args == ("An unrelated result: <not HTML>.",)
    assert prose.kwargs == {}
    assert "&lt;Desk &amp; lamp&gt;" in entity_list.args[0]
    assert entity_list.kwargs == {"parse_mode": ParseMode.HTML}
    [exchange] = await history.get_exchanges()
    assert "<Desk & lamp>" in exchange.reply
    assert "<b>" not in exchange.reply
    assert "light.desk" not in exchange.reply
