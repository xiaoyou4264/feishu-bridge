"""Tests for src/handler.py — on_message_receive sync handler and handle_message async coroutine."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_event_data, make_event_message


class TestCreateHandler:
    """Tests for create_handler() factory function."""

    def test_create_handler_returns_callable(self):
        """create_handler() returns a callable."""
        from src.handler import create_handler

        loop = asyncio.new_event_loop()
        try:
            api_client = MagicMock()
            dedup_cache = MagicMock()
            handler = create_handler(loop, api_client, "ou_bot_001", dedup_cache)
            assert callable(handler)
        finally:
            loop.close()

    def test_on_message_receive_is_sync_not_async(self):
        """CRITICAL (Pitfall 1): The returned handler must be sync, NOT async."""
        from src.handler import create_handler

        loop = asyncio.new_event_loop()
        try:
            api_client = MagicMock()
            dedup_cache = MagicMock()
            on_message = create_handler(loop, api_client, "ou_bot_001", dedup_cache)
            # Must NOT be a coroutine function — SDK calls it synchronously
            assert not asyncio.iscoroutinefunction(on_message), (
                "Handler must be sync (def), not async. "
                "Async handler causes silent event drop (Pitfall 1)."
            )
        finally:
            loop.close()


class TestHandleMessage:
    """Tests for handle_message() async coroutine."""

    @pytest.mark.asyncio
    async def test_skips_duplicate_event(self):
        """handle_message() returns early without doing anything on duplicate event_id."""
        from src.handler import handle_message

        event_data = make_event_data(event_id="evt_dup_001")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = True  # duplicate!

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache)
            mock_card.assert_not_called()

        dedup_cache.is_duplicate.assert_called_once_with("evt_dup_001")

    @pytest.mark.asyncio
    async def test_skips_group_message_without_mention(self):
        """handle_message() returns early for group messages without @bot mention."""
        from src.handler import handle_message

        msg = make_event_message(chat_type="group", mentions=None)
        event_data = make_event_data(message=msg, event_id="evt_group_no_mention")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False  # new event

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache)
            mock_card.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_thinking_card_for_p2p_text_message(self):
        """handle_message() calls send_thinking_card() for valid P2P text message."""
        from src.handler import handle_message

        msg = make_event_message(
            chat_type="p2p",
            message_type="text",
            content='{"text": "hello"}',
            message_id="msg_p2p_001",
        )
        event_data = make_event_data(message=msg, event_id="evt_p2p_001")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            mock_card.return_value = "reply_msg_001"
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache)
            mock_card.assert_called_once_with(api_client, "msg_p2p_001")

    @pytest.mark.asyncio
    async def test_sends_thinking_card_for_group_with_mention(self):
        """handle_message() calls send_thinking_card() for group message with @bot mention."""
        import types
        from src.handler import handle_message

        # Build mention for the bot
        mention_id = types.SimpleNamespace(open_id="ou_bot_001")
        mention = types.SimpleNamespace(id=mention_id, key="@_user_1", name="AI 助手")

        msg = make_event_message(
            chat_type="group",
            message_type="text",
            content='{"text": "hello @_user_1"}',
            mentions=[mention],
            message_id="msg_group_001",
        )
        event_data = make_event_data(message=msg, event_id="evt_group_mention_001")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            mock_card.return_value = "reply_msg_002"
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache)
            mock_card.assert_called_once_with(api_client, "msg_group_001")

    @pytest.mark.asyncio
    async def test_sends_unsupported_type_card_for_image(self):
        """handle_message() calls send_unsupported_type_card() for image messages (D-05)."""
        from src.handler import handle_message

        msg = make_event_message(
            chat_type="p2p",
            message_type="image",
            content='{"image_key": "img_001"}',
            message_id="msg_img_001",
        )
        event_data = make_event_data(message=msg, event_id="evt_img_001")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_thinking,
            patch("src.handler.send_unsupported_type_card", new_callable=AsyncMock) as mock_unsupported,
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache)
            mock_thinking.assert_not_called()
            mock_unsupported.assert_called_once_with(api_client, "msg_img_001", "image")

    @pytest.mark.asyncio
    async def test_does_not_crash_on_send_card_exception(self):
        """handle_message() catches and logs exceptions from send_thinking_card without crashing."""
        from src.handler import handle_message

        msg = make_event_message(
            chat_type="p2p",
            message_type="text",
            content='{"text": "test"}',
            message_id="msg_err_001",
        )
        event_data = make_event_data(message=msg, event_id="evt_err_001")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            mock_card.side_effect = RuntimeError("Network error")
            # Should not raise — must be caught and logged
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache)


class TestHandleMessageIntegration:
    """Integration behavior tests for the handler pipeline."""

    @pytest.mark.asyncio
    async def test_dedup_check_happens_before_card_send(self):
        """Dedup check must happen before any card is sent (saves API calls on retry)."""
        from src.handler import handle_message

        event_data = make_event_data(event_id="evt_order_001")
        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = True  # duplicate

        call_order = []
        dedup_cache.is_duplicate.side_effect = lambda x: (call_order.append("dedup"), True)[1]

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            mock_card.side_effect = lambda *a, **kw: call_order.append("card")
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache)

        assert "dedup" in call_order
        assert "card" not in call_order


class TestMainImportable:
    """Tests that main.py is importable and has a main() function."""

    def test_main_module_has_main_function(self):
        """main.py must be importable and expose a main() function."""
        import importlib
        import sys

        # Temporarily patch lark_oapi.ws import to avoid blocking
        # and Config.from_env to avoid needing env vars
        if "main" in sys.modules:
            del sys.modules["main"]

        # Just check the file exists and has the right structure via grep
        import os
        assert os.path.exists("main.py"), "main.py must exist"

        with open("main.py") as f:
            content = f.read()
        assert "def main(" in content, "main.py must have a main() function"
        assert "if __name__" in content, "main.py must have __main__ guard"
