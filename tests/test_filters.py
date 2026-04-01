"""Tests for src/filters.py — should_respond() and parse_message_content()."""
import types
import json
import pytest

from tests.conftest import make_event_message
from src.filters import should_respond, parse_message_content


BOT_OPEN_ID = "ou_bot_12345"


def make_mention(open_id: str, key: str = "@_user_1", name: str = "Bot") -> types.SimpleNamespace:
    """Helper to create a mention-like object."""
    mention_id = types.SimpleNamespace(open_id=open_id)
    return types.SimpleNamespace(id=mention_id, key=key, name=name)


class TestShouldRespond:
    def test_p2p_returns_true_without_mentions(self):
        """P2P message always returns True regardless of mentions."""
        msg = make_event_message(chat_type="p2p", mentions=None)
        assert should_respond(msg, BOT_OPEN_ID) is True

    def test_p2p_returns_true_with_mentions(self):
        """P2P message returns True even when mentions list is present."""
        msg = make_event_message(chat_type="p2p", mentions=[make_mention(BOT_OPEN_ID)])
        assert should_respond(msg, BOT_OPEN_ID) is True

    def test_group_with_bot_mention_returns_true(self):
        """Group message with bot in mentions returns True."""
        bot_mention = make_mention(BOT_OPEN_ID)
        msg = make_event_message(chat_type="group", mentions=[bot_mention])
        assert should_respond(msg, BOT_OPEN_ID) is True

    def test_group_without_mentions_returns_false(self):
        """Group message without any mentions returns False."""
        msg = make_event_message(chat_type="group", mentions=None)
        assert should_respond(msg, BOT_OPEN_ID) is False

    def test_group_with_empty_mentions_returns_false(self):
        """Group message with empty mentions list returns False."""
        msg = make_event_message(chat_type="group", mentions=[])
        assert should_respond(msg, BOT_OPEN_ID) is False

    def test_group_with_other_user_mentioned_returns_false(self):
        """Group message with another user @mentioned (not bot) returns False."""
        other_mention = make_mention("ou_other_user")
        msg = make_event_message(chat_type="group", mentions=[other_mention])
        assert should_respond(msg, BOT_OPEN_ID) is False

    def test_unknown_chat_type_returns_false(self):
        """Unknown chat_type returns False."""
        msg = make_event_message(chat_type="unknown")
        assert should_respond(msg, BOT_OPEN_ID) is False


class TestParseMessageContent:
    def test_text_message_returns_text_and_type(self):
        """Text message '{"text":"hello"}' returns ("hello", "text")."""
        msg = make_event_message(
            message_type="text",
            content=json.dumps({"text": "hello"}),
        )
        text, msg_type = parse_message_content(msg)
        assert text == "hello"
        assert msg_type == "text"

    def test_text_message_strips_mention_placeholder(self):
        """Text message with @mention strips mention placeholder from text."""
        mention = make_mention(BOT_OPEN_ID, key="@_user_1", name="AI Bot")
        msg = make_event_message(
            message_type="text",
            content=json.dumps({"text": "@_user_1 hello bot"}),
            mentions=[mention],
        )
        text, msg_type = parse_message_content(msg)
        assert "@_user_1" not in text
        assert "hello bot" in text
        assert msg_type == "text"

    def test_post_message_extracts_plain_text(self):
        """Post (rich text) message extracts plain text from content nodes."""
        rich_content = {
            "zh_cn": {
                "title": "Title",
                "content": [
                    [{"tag": "text", "text": "Hello"}, {"tag": "text", "text": " World"}]
                ],
            }
        }
        msg = make_event_message(
            message_type="post",
            content=json.dumps(rich_content),
        )
        text, msg_type = parse_message_content(msg)
        assert "Hello" in text
        assert "World" in text
        assert msg_type == "post"

    def test_image_message_raises_value_error(self):
        """Image message type raises ValueError with 'unsupported_type:image'."""
        msg = make_event_message(
            message_type="image",
            content=json.dumps({"image_key": "img_001"}),
        )
        with pytest.raises(ValueError) as exc_info:
            parse_message_content(msg)
        assert "unsupported_type:image" in str(exc_info.value)

    def test_file_message_raises_value_error(self):
        """File message type raises ValueError with 'unsupported_type:file'."""
        msg = make_event_message(
            message_type="file",
            content=json.dumps({"file_key": "file_001"}),
        )
        with pytest.raises(ValueError) as exc_info:
            parse_message_content(msg)
        assert "unsupported_type:file" in str(exc_info.value)

    def test_unsupported_types_raise_value_error(self):
        """Per D-05, unsupported types raise ValueError (caller sends friendly prompt)."""
        for msg_type in ("audio", "video", "sticker"):
            msg = make_event_message(message_type=msg_type, content="{}")
            with pytest.raises(ValueError) as exc_info:
                parse_message_content(msg)
            assert f"unsupported_type:{msg_type}" in str(exc_info.value)
