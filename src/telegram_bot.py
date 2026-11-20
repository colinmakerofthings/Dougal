"""Telegram polling setup and private-user message handling."""

import asyncio
import logging
from datetime import timedelta

import httpx
from openai import RateLimitError
from telegram import Update, Voice
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
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
_MAX_VOICE_SECONDS = 30
_MAX_VOICE_BYTES = 2 * 1024 * 1024


async def _transcribe_voice(voice: Voice, url: str, model: str) -> str:
    async with asyncio.timeout(30):
        telegram_file = await voice.get_file()
        if telegram_file.file_size is not None and telegram_file.file_size > _MAX_VOICE_BYTES:
            raise ValueError("Voice note exceeds the size limit")
        audio = await telegram_file.download_as_bytearray()
        if not audio or len(audio) > _MAX_VOICE_BYTES:
            raise ValueError("Invalid voice note size")
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                url,
                files={"file": ("voice.ogg", bytes(audio), "audio/ogg")},
                data={"model": model, "response_format": "json"},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("No transcript returned")
    return text.strip()


def build_telegram_application(
    settings: Settings,
    agent: DougalAgent,
    history: MessageHistory | None = None,
) -> Application:
    application = Application.builder().token(
        settings.telegram_bot_token.get_secret_value()
    ).build()
    allowed_ids = settings.allowed_telegram_user_ids
    transcription_url = settings.dougal_voice_transcription_url

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
        text = message.text
        is_voice = False
        if not text:
            voice = message.voice if transcription_url is not None else None
            if voice is None or transcription_url is None:
                return
            duration = voice.duration
            seconds = (
                duration.total_seconds() if isinstance(duration, timedelta) else duration
            )
            if seconds > _MAX_VOICE_SECONDS or (
                voice.file_size is not None and voice.file_size > _MAX_VOICE_BYTES
            ):
                await message.reply_text("Please send a voice note up to 30 seconds and 2 MiB.")
                return
            is_voice = True
            if history is not None:
                history.record_received()
            try:
                text = await _transcribe_voice(
                    voice, str(transcription_url), settings.dougal_voice_transcription_model
                )
            except (TelegramError, httpx.HTTPError, TimeoutError, ValueError) as error:
                logger.warning("Voice transcription failed (%s)", type(error).__name__)
                await message.reply_text(
                    "I couldn't transcribe that voice note. Please try again or send text."
                )
                return
        elif history is not None:
            history.record_received()
        try:
            answer = (
                await agent.respond(text, read_only=True)
                if is_voice
                else await agent.respond(text)
            )
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
            history.add(text, history_answer, user_name=user.full_name, is_voice=is_voice)

    message_filter = filters.TEXT & ~filters.COMMAND
    if transcription_url is not None:
        message_filter = message_filter | filters.VOICE
    application.add_handler(MessageHandler(message_filter, handle_message))
    return application
