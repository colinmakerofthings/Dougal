"""Persistent Telegram activity and 30-day completed-exchange history."""

import asyncio
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_RETENTION = timedelta(days=30)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MessageExchange:
    recorded_at: datetime
    user_message: str
    reply: str
    user_name: str = "User"
    is_voice: bool = False


@dataclass(frozen=True)
class HistoryStatus:
    last_message_received_at: datetime | None
    text_messages: int
    voice_messages: int


class MessageHistory:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialized = False
        self._init_lock = asyncio.Lock()

    @contextmanager
    def _database(self) -> Generator[sqlite3.Connection, None, None]:
        with closing(sqlite3.connect(self._path, timeout=10)) as connection:
            connection.execute("PRAGMA secure_delete = ON")
            with connection:
                yield connection

    async def initialize(self) -> None:
        async with self._init_lock:
            if not self._initialized:
                await asyncio.to_thread(self._initialize)
                self._initialized = True

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._database() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS exchanges ("
                "id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, "
                "user_message TEXT NOT NULL, reply TEXT NOT NULL, "
                "user_name TEXT NOT NULL, is_voice INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS exchanges_recorded_at "
                "ON exchanges(recorded_at)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS activity ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), received_at TEXT NOT NULL)"
            )
            self._delete_expired(connection, _utcnow())

    @staticmethod
    def _delete_expired(connection: sqlite3.Connection, now: datetime) -> None:
        cutoff = (now - _RETENTION).isoformat()
        connection.execute("DELETE FROM exchanges WHERE recorded_at <= ?", (cutoff,))
        connection.execute("DELETE FROM activity WHERE received_at <= ?", (cutoff,))

    async def purge_expired(self) -> None:
        await self.initialize()
        await asyncio.to_thread(self._purge_expired, _utcnow())

    def _purge_expired(self, now: datetime) -> None:
        with self._database() as connection:
            self._delete_expired(connection, now)

    async def record_received(self) -> None:
        await self.initialize()
        await asyncio.to_thread(self._record_received, _utcnow())

    def _record_received(self, now: datetime) -> None:
        with self._database() as connection:
            connection.execute(
                "INSERT INTO activity (id, received_at) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET received_at = excluded.received_at "
                "WHERE excluded.received_at > activity.received_at",
                (now.isoformat(),),
            )

    async def add(
        self, user_message: str, reply: str, *, user_name: str = "User",
        is_voice: bool = False,
    ) -> None:
        await self.initialize()
        await asyncio.to_thread(
            self._add, _utcnow(), user_message, reply, user_name, is_voice
        )

    def _add(
        self, now: datetime, user_message: str, reply: str, user_name: str,
        is_voice: bool,
    ) -> None:
        with self._database() as connection:
            connection.execute(
                "INSERT INTO exchanges "
                "(recorded_at, user_message, reply, user_name, is_voice) "
                "VALUES (?, ?, ?, ?, ?)",
                (now.isoformat(), user_message, reply, user_name, int(is_voice)),
            )

    async def get_exchanges(self) -> tuple[MessageExchange, ...]:
        await self.initialize()
        return await asyncio.to_thread(self._get_exchanges, _utcnow())

    def _get_exchanges(self, now: datetime) -> tuple[MessageExchange, ...]:
        with self._database() as connection:
            self._delete_expired(connection, now)
            rows = connection.execute(
                "SELECT recorded_at, user_message, reply, user_name, is_voice "
                "FROM exchanges ORDER BY recorded_at, id"
            ).fetchall()
        return tuple(
            MessageExchange(datetime.fromisoformat(time), message, reply, name, bool(voice))
            for time, message, reply, name, voice in rows
        )

    async def get_status(self) -> HistoryStatus:
        await self.initialize()
        return await asyncio.to_thread(self._get_status, _utcnow())

    def _get_status(self, now: datetime) -> HistoryStatus:
        with self._database() as connection:
            self._delete_expired(connection, now)
            received = connection.execute(
                "SELECT received_at FROM activity WHERE id = 1"
            ).fetchone()
            counts = dict(connection.execute(
                "SELECT is_voice, COUNT(*) FROM exchanges GROUP BY is_voice"
            ).fetchall())
        return HistoryStatus(
            datetime.fromisoformat(received[0]) if received else None,
            counts.get(0, 0),
            counts.get(1, 0),
        )
