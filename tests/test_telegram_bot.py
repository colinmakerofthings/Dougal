import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import RateLimitError
from telegram import Update
from telegram.constants import ChatType

from src.agent import DougalAgent
from src.config import Settings
from src.history import MessageHistory
from src.telegram_bot import build_telegram_application


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
    history = MessageHistory()
    application = build_telegram_application(settings, agent, history=history)
    update = MagicMock(spec=Update)
    update.effective_user.id = 123
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
    assert len(history.exchanges) == 1
    assert history.exchanges[0].reply == reply
    assert "private-provider-details" not in history.exchanges[0].reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "chat_type", "should_record"),
    [
        (123, ChatType.PRIVATE, True),
        (999, ChatType.PRIVATE, False),
        (123, ChatType.GROUP, False),
    ],
)
async def test_only_authorized_private_exchanges_are_recorded(
    user_id: int, chat_type: str, should_record: bool
) -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        allowed_telegram_user_ids={123},
        openai_api_key="test-key",
        home_assistant_url="http://ha.local",
        home_assistant_token="test-token",
    )
    history = MessageHistory()
    agent = MagicMock(spec=DougalAgent)
    answer = "A" * 4100
    agent.respond = AsyncMock(return_value=answer)
    application = build_telegram_application(settings, agent, history=history)
    update = MagicMock(spec=Update)
    update.effective_user.id = user_id
    update.effective_chat.type = chat_type
    update.effective_message.text = "Hello"
    update.effective_message.reply_text = AsyncMock()

    await application.handlers[0][0].callback(update, MagicMock())

    if should_record:
        assert len(history.exchanges) == 1
        assert history.exchanges[0].user_message == "Hello"
        assert history.exchanges[0].reply == answer
        assert update.effective_message.reply_text.await_count == 2
        assert "".join(
            call.args[0] for call in update.effective_message.reply_text.await_args_list
        ) == answer
    else:
        assert history.exchanges == ()
        agent.respond.assert_not_awaited()
        update.effective_message.reply_text.assert_not_awaited()
