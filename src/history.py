"""Bounded, process-local history of completed Telegram exchanges."""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class MessageExchange:
    recorded_at: datetime
    user_message: str
    reply: str
    user_name: str = "User"


class MessageHistory:
    def __init__(self) -> None:
        self._exchanges: deque[MessageExchange] = deque(maxlen=100)

    def add(
        self, user_message: str, reply: str, *, user_name: str = "User"
    ) -> None:
        self._exchanges.append(
            MessageExchange(datetime.now(UTC), user_message, reply, user_name)
        )

    @property
    def exchanges(self) -> tuple[MessageExchange, ...]:
        return tuple(self._exchanges)
