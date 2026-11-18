"""Telegram polling setup and private-user message handling."""

import logging

from openai import RateLimitError
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from src.agent import DougalAgent
from src.config import Settings
from src.entity_responses import (
    EntityResponse,
    plain_entity_text,
    render_entity_list,
    split_text,
)
from src.history import MessageHistory

logger = logging.getLogger(__name__)


def build_telegram_application(
    settings: Settings,
    agent: DougalAgent,
    history: MessageHistory | None = None,
) -> Application:
    application = Application.builder().token(
        settings.telegram_bot_token.get_secret_value()
    ).build()
    allowed_ids = settings.allowed_telegram_user_ids

    async def handle_message(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        user = update.effective_user
        message = update.effective_message
        if (
            user is None
            or message is None
            or update.effective_chat is None
            or update.effective_chat.type != ChatType.PRIVATE
            or user.id not in allowed_ids
        ):
            return
        if not message.text:
            return
        if history is not None:
            history.record_received()
        try:
            answer = await agent.respond(message.text)
        except RateLimitError as error:
            if (
                error.code in ("credit_balance_exhausted", "insufficient_quota")
                or error.type == "insufficient_quota"
            ):
                logger.warning("AI provider quota exhausted")
                answer = (
                    "Ah, I'm afraid I've run out of API credits. "
                    "I checked down the back of the sofa, but no luck. "
                    "Please top up the OpenAI API balance, then try me again."
                )
            else:
                logger.warning("AI provider rate limit reached")
                answer = (
                    "Ah, I seem to be getting ahead of myself. "
                    "The AI service needs a breather; please try again in a little while."
                )
        except Exception as error:
            logger.error(
                "Failed to process an authorized Telegram message (%s)",
                type(error).__name__,
            )
            answer = (
                "Ah, something went wrong while handling that. "
                "I couldn't complete the request just now; please try again."
            )
        if isinstance(answer, EntityResponse):
            history_parts = [answer.text] if answer.text else []
            for part in split_text(answer.text):
                await message.reply_text(part)
            for report in answer.lists:
                for part in render_entity_list(report):
                    await message.reply_text(part, parse_mode=ParseMode.HTML)
                    history_parts.append(plain_entity_text(part))
            history_answer = "\n\n".join(history_parts)
        else:
            for part in split_text(answer):
                await message.reply_text(part)
            history_answer = answer
        if history is not None:
            history.add(message.text, history_answer, user_name=user.full_name)

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    return application
