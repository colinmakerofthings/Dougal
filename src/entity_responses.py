"""Reusable, mobile-friendly Telegram presentation of entity discovery results."""

import re
from dataclasses import dataclass
from html import escape, unescape
from typing import TypedDict


TELEGRAM_MESSAGE_LIMIT = 4096


class Entity(TypedDict):
    entity_id: str
    name: str
    state: str


@dataclass
class EntityList:
    domain: str
    entities: list[Entity]
    area: str | None = None
    state: str | None = None
    show_entity_ids: bool = False


@dataclass
class EntityResponse:
    lists: list[EntityList]
    text: str = ""


_DOMAIN_TITLES = {
    "light": "\U0001f4a1 Home Lights",
    "switch": "\U0001f50c Switches",
    "sensor": "\U0001f321\ufe0f Sensors",
    "binary_sensor": "\U0001f4e1 Binary Sensors",
    "cover": "\U0001f6aa Covers",
    "lock": "\U0001f512 Locks",
}
_STATE_ICONS = {
    "on": "\U0001f7e2",
    "off": "\u26ab",
    "open": "\U0001f7e2",
    "closed": "\u26ab",
    "unavailable": "\u26a0\ufe0f",
    "unknown": "\u2754",
}


def _length(text: str) -> int:
    # UTF-16 is conservative for Telegram's character limit and emoji offsets.
    return len(text.encode("utf-16-le")) // 2


def split_text(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split plain text without cutting a Unicode code point."""
    parts: list[str] = []
    start = 0
    size = 0
    for index, character in enumerate(text):
        width = _length(character)
        if size + width > limit:
            parts.append(text[start:index])
            start = index
            size = 0
        size += width
    if start < len(text):
        parts.append(text[start:])
    return parts


def _escaped_parts(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for character in text:
        token = escape(character, quote=False)
        width = _length(token)
        if size + width > limit:
            parts.append("".join(current))
            current = []
            size = 0
        current.append(token)
        size += width
    if current:
        parts.append("".join(current))
    return parts


def _label(text: str) -> str:
    """Keep repeated headings bounded, even for unusually large state values."""
    text = " ".join(text.split())
    return escape(text[:80] + ("..." if len(text) > 80 else ""), quote=False)


def render_entity_list(report: EntityList) -> list[str]:
    """Return independently valid HTML messages, with headings on every page."""
    title = _DOMAIN_TITLES.get(
        report.domain, f"\U0001f4e1 {_label(report.domain.replace('_', ' ').title())}"
    )
    if report.area:
        title += f" - {_label(report.area)}"
    title = f"<b>{title}</b>"
    groups: dict[str, list[Entity]] = {}
    for entity in report.entities:
        if report.state is None or entity["state"] == report.state:
            groups.setdefault(entity["state"], []).append(entity)
    if not groups:
        message = (
            "No matching entities are visible under the current filters."
            if not report.entities
            else f"No entities are reporting as {_label(report.state or '')}."
        )
        return [f"{title}\n\n{message}"]

    messages: list[str] = []
    page = title
    ordered_states = sorted(
        groups, key=lambda state: ({"on": 0, "off": 1}.get(state, 2), state)
    )
    for state in ordered_states:
        entities = groups[state]
        icon = _STATE_ICONS.get(state, "\U0001f539")
        heading = (
            f"{icon} <b>{_label(state.upper())} ({len(entities)})</b>"
        )
        prefix = f"{title}\n\n{heading}"
        needs_heading = True
        budget = TELEGRAM_MESSAGE_LIMIT - _length(prefix) - 32
        for entity in sorted(entities, key=lambda item: item["name"].casefold()):
            name = " ".join(entity["name"].split())
            if not name or name == entity["entity_id"]:
                name = entity["entity_id"].partition(".")[2].replace("_", " ").capitalize()
            parts = _escaped_parts(name or "Unnamed entity", budget)
            rows = [f"\u2022 {parts[0]}", *(f"  {part}" for part in parts[1:])]
            if report.show_entity_ids:
                rows.extend(
                    f"  <code>{part}</code>"
                    for part in _escaped_parts(entity["entity_id"], budget)
                )
            for row in rows:
                separator = f"\n\n{heading}" if needs_heading else ""
                candidate = f"{page}{separator}\n{row}"
                if _length(candidate) > TELEGRAM_MESSAGE_LIMIT:
                    messages.append(page)
                    page = f"{prefix}\n{row}"
                else:
                    page = candidate
                needs_heading = False
    messages.append(page)
    return messages


def plain_entity_text(html: str) -> str:
    """Remove only our generated tags before storing a readable history entry."""
    return unescape(re.sub(r"</?(?:b|code)>", "", html))
