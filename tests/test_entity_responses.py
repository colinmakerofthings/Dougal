from html.parser import HTMLParser

import pytest

from src.entity_responses import (
    Entity,
    EntityList,
    plain_entity_text,
    render_entity_list,
    split_text,
)


class TelegramHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        assert tag in {"b", "code"}
        assert not attrs
        self.tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.tags.pop() == tag


def test_grouped_names_are_escaped_sorted_and_ids_hidden() -> None:
    report = EntityList(
        domain="light",
        area="Kitchen <&>",
        entities=[
            {"entity_id": "light.z", "name": "Zebra", "state": "on"},
            {"entity_id": "light.a", "name": "<b>A & B</b>", "state": "on"},
            {"entity_id": "light.hall_lamp", "name": "light.hall_lamp", "state": "off"},
            {"entity_id": "light.u", "name": "Missing", "state": "unavailable"},
            {"entity_id": "light.q", "name": "Uncertain", "state": "unknown"},
        ],
    )

    [message] = render_entity_list(report)

    assert "\U0001f4a1 Home Lights - Kitchen &lt;&amp;&gt;" in message
    assert "\U0001f7e2 <b>ON (2)</b>" in message
    assert "\u26ab <b>OFF (1)</b>" in message
    assert "<b>UNAVAILABLE (1)</b>" in message
    assert "<b>UNKNOWN (1)</b>" in message
    assert message.index("&lt;b&gt;A &amp; B&lt;/b&gt;") < message.index("Zebra")
    assert "Hall lamp" in message
    assert "light." not in message
    assert "<b>A & B</b>" in plain_entity_text(message)


@pytest.mark.parametrize("show_ids", [False, True])
def test_state_filter_and_optional_debug_ids(show_ids: bool) -> None:
    [message] = render_entity_list(
        EntityList(
            domain="switch",
            entities=[
                {"entity_id": "switch.a", "name": "Desk", "state": "on"},
                {"entity_id": "switch.b", "name": "Fan", "state": "off"},
            ],
            state="on",
            show_entity_ids=show_ids,
        )
    )
    assert "\U0001f50c Switches" in message
    assert "ON (1)" in message
    assert "Fan" not in message
    assert ("<code>switch.a</code>" in message) == show_ids
    assert "switch.b" not in message


def test_off_state_filter_excludes_on_and_unavailable_entities() -> None:
    [message] = render_entity_list(
        EntityList(
            domain="light",
            entities=[
                {"entity_id": "light.on", "name": "On lamp", "state": "on"},
                {"entity_id": "light.off", "name": "Off lamp", "state": "off"},
                {
                    "entity_id": "light.unavailable",
                    "name": "Unavailable lamp",
                    "state": "unavailable",
                },
            ],
            state="off",
        )
    )

    assert "OFF (1)" in message
    assert "Off lamp" in message
    assert "ON" not in message
    assert "On lamp" not in message
    assert "UNAVAILABLE" not in message
    assert "Unavailable lamp" not in message


def test_unavailable_state_filter_excludes_off_and_unknown_entities() -> None:
    [message] = render_entity_list(
        EntityList(
            domain="light",
            entities=[
                {"entity_id": "light.off", "name": "Off lamp", "state": "off"},
                {
                    "entity_id": "light.unavailable",
                    "name": "Unavailable lamp",
                    "state": "unavailable",
                },
                {"entity_id": "light.unknown", "name": "Unknown lamp", "state": "unknown"},
            ],
            state="unavailable",
        )
    )

    assert "UNAVAILABLE (1)" in message
    assert "Unavailable lamp" in message
    assert "OFF" not in message
    assert "Off lamp" not in message
    assert "UNKNOWN" not in message
    assert "Unknown lamp" not in message


@pytest.mark.parametrize(
    ("entities", "state", "expected"),
    [
        ([], None, "No matching entities are visible"),
        ([], "on", "No matching entities are visible"),
        (
            [{"entity_id": "light.u", "name": "Lamp", "state": "unavailable"}],
            "on",
            "No entities are reporting as on",
        ),
    ],
)
def test_empty_results_are_not_claimed_to_be_off(
    entities: list[Entity], state: str | None, expected: str
) -> None:
    [message] = render_entity_list(EntityList("light", entities, state=state))
    assert expected in message
    assert "OFF" not in message


def test_other_domains_preserve_non_binary_states() -> None:
    [message] = render_entity_list(
        EntityList(
            "sensor",
            [
                {"entity_id": "sensor.a", "name": "Temperature", "state": "21.5"},
                {"entity_id": "sensor.b", "name": "Battery", "state": "73"},
            ],
        )
    )
    assert "Sensors" in message
    assert "<b>21.5 (1)</b>" in message
    assert "<b>73 (1)</b>" in message
    assert "ON" not in message
    assert "OFF" not in message


def test_large_lists_and_single_oversize_names_produce_valid_bounded_html() -> None:
    entities: list[Entity] = [
        {"entity_id": f"light.lamp_{index}", "name": f"Lamp {index:04}", "state": "on"}
        for index in range(600)
    ]
    long_name = "\U0001f4a1<&>" * 2500
    entities.append({"entity_id": "light.long", "name": long_name, "state": "on"})
    messages = render_entity_list(EntityList("light", entities, show_entity_ids=True))

    assert len(messages) > 3
    combined = "\n".join(plain_entity_text(message) for message in messages)
    for message in messages:
        assert len(message.encode("utf-16-le")) // 2 <= 4096
        parser = TelegramHTML()
        parser.feed(message)
        parser.close()
        assert parser.tags == []
        assert message.startswith("<b>\U0001f4a1 Home Lights</b>")
        assert "<b>ON (601)</b>" in message
    for index in range(600):
        assert combined.count(f"light.lamp_{index}\n") == 1
    name_parts = [
        line[2:]
        for line in combined.splitlines()
        if line.startswith(("\u2022 ", "  "))
        and not line[2:].startswith(("Lamp ", "light."))
    ]
    assert "".join(name_parts) == long_name
    assert combined.count("light.long") == 1


def test_plain_splitting_respects_emoji_limit_without_losing_text() -> None:
    text = "A" * 4095 + "\U0001f4a1" * 4097
    parts = split_text(text)
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in parts)
