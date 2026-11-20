import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.history import MessageHistory


@pytest.mark.asyncio
async def test_retention_purges_expired_records_and_preserves_newer_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "history.sqlite3"
    history = MessageHistory(path)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=30)

    with patch("src.history._utcnow", return_value=cutoff):
        await history.record_received()
        await history.add("expired", "expired reply")
    with patch("src.history._utcnow", return_value=cutoff + timedelta(microseconds=1)):
        await history.add("retained", "retained reply", is_voice=True)

    with patch("src.history._utcnow", return_value=now):
        restarted = MessageHistory(path)
        await restarted.initialize()
        [exchange] = await restarted.get_exchanges()
        status = await restarted.get_status()

    assert exchange.user_message == "retained"
    assert exchange.is_voice
    assert status.last_message_received_at is None
    assert status.text_messages == 0
    assert status.voice_messages == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM exchanges").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM activity").fetchone() == (0,)


@pytest.mark.asyncio
async def test_periodic_cleanup_purges_without_reading_history(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    history = MessageHistory(path)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    with patch("src.history._utcnow", return_value=now):
        await history.record_received()
        await history.add("message", "reply")
    with patch("src.history._utcnow", return_value=now + timedelta(days=30)):
        await history.purge_expired()

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM exchanges").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM activity").fetchone() == (0,)


@pytest.mark.asyncio
async def test_history_reads_exclude_records_that_expire_after_startup(tmp_path: Path) -> None:
    history = MessageHistory(tmp_path / "history.sqlite3")
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    with patch("src.history._utcnow", return_value=now):
        await history.record_received()
        await history.add("message", "reply")
    with patch("src.history._utcnow", return_value=now + timedelta(days=30)):
        assert await history.get_exchanges() == ()
        status = await history.get_status()

    assert status.last_message_received_at is None
    assert status.text_messages == 0
    assert status.voice_messages == 0


@pytest.mark.asyncio
async def test_concurrent_writes_are_retained(tmp_path: Path) -> None:
    history = MessageHistory(tmp_path / "history.sqlite3")
    await asyncio.gather(*(history.add(str(index), "reply") for index in range(20)))

    exchanges = await MessageHistory(tmp_path / "history.sqlite3").get_exchanges()
    assert {exchange.user_message for exchange in exchanges} == {
        str(index) for index in range(20)
    }
