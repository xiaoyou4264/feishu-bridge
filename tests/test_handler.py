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
            session_manager = MagicMock()
            config = MagicMock()
            handler = create_handler(loop, api_client, "ou_bot_001", dedup_cache, session_manager, config)
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
            session_manager = MagicMock()
            config = MagicMock()
            on_message = create_handler(loop, api_client, "ou_bot_001", dedup_cache, session_manager, config)
            # Must NOT be a coroutine function — SDK calls it synchronously
            assert not asyncio.iscoroutinefunction(on_message), (
                "Handler must be sync (def), not async. "
                "Async handler causes silent event drop (Pitfall 1)."
            )
        finally:
            loop.close()

    def test_create_handler_accepts_session_manager(self):
        """create_handler() accepts session_manager and config parameters."""
        from src.handler import create_handler
        import inspect

        sig = inspect.signature(create_handler)
        params = list(sig.parameters.keys())
        assert "session_manager" in params, "create_handler must accept session_manager"
        assert "config" in params, "create_handler must accept config"


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
        session_manager = MagicMock()
        config = MagicMock()

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)
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
        session_manager = MagicMock()
        config = MagicMock()

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)
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
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=MagicMock(name_cache={}))
        session_manager.semaphore = MagicMock()
        config = MagicMock()
        config.claude_timeout = 120.0

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card,
            patch("src.handler.single_turn_worker", new_callable=AsyncMock),
            patch("src.handler.get_display_name", new_callable=AsyncMock, return_value="Alice"),
            patch("asyncio.create_task"),
        ):
            mock_card.return_value = "reply_msg_001"
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)
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
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=MagicMock(name_cache={}))
        session_manager.semaphore = MagicMock()
        config = MagicMock()
        config.claude_timeout = 120.0

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card,
            patch("src.handler.single_turn_worker", new_callable=AsyncMock),
            patch("src.handler.get_display_name", new_callable=AsyncMock, return_value="Alice"),
            patch("asyncio.create_task"),
        ):
            mock_card.return_value = "reply_msg_002"
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)
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
        session_manager = MagicMock()
        config = MagicMock()

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_thinking,
            patch("src.handler.send_unsupported_type_card", new_callable=AsyncMock) as mock_unsupported,
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)
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
        session_manager = MagicMock()
        config = MagicMock()

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            mock_card.side_effect = RuntimeError("Network error")
            # Should not raise — must be caught and logged
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)


class TestHandleMessageClaudeDispatch:
    """Tests for Claude dispatch in handle_message()."""

    @pytest.mark.asyncio
    async def test_handle_message_dispatches_claude_task(self):
        """After sending thinking card, handle_message creates an asyncio.Task for single_turn_worker."""
        from src.handler import handle_message

        msg = make_event_message(
            chat_type="p2p",
            message_type="text",
            content='{"text": "hello"}',
            message_id="msg_dispatch_001",
        )
        event_data = make_event_data(message=msg, event_id="evt_dispatch_001")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        mock_session = MagicMock()
        mock_session.name_cache = {}
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=mock_session)
        mock_semaphore = MagicMock()
        session_manager.semaphore = mock_semaphore
        config = MagicMock()
        config.claude_timeout = 120.0

        tasks_created = []

        def capture_task(coro):
            tasks_created.append(coro)
            # Return a mock task
            return MagicMock()

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock, return_value="reply_001"),
            patch("src.handler.single_turn_worker", new_callable=AsyncMock) as mock_worker,
            patch("asyncio.create_task", side_effect=capture_task),
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)

        assert len(tasks_created) == 1, "Should create exactly one asyncio.Task"

    @pytest.mark.asyncio
    async def test_handle_message_p2p_session_key_is_open_id(self):
        """For P2P messages, get_session_key is called with chat_type='p2p' and sender's open_id."""
        from src.handler import handle_message

        msg = make_event_message(
            chat_type="p2p",
            message_type="text",
            content='{"text": "hello"}',
        )
        event_data = make_event_data(message=msg, sender_open_id="ou_p2p_sender")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        mock_session = MagicMock()
        mock_session.name_cache = {}
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=mock_session)
        session_manager.semaphore = MagicMock()
        config = MagicMock()
        config.claude_timeout = 120.0

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock, return_value="reply_001"),
            patch("src.handler.single_turn_worker", new_callable=AsyncMock),
            patch("asyncio.create_task"),
            patch("src.handler.get_session_key", wraps=__import__("src.session", fromlist=["get_session_key"]).get_session_key) as mock_gsk,
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)

        # P2P: key should be sender open_id
        mock_gsk.assert_called_once()
        call_args = mock_gsk.call_args
        assert call_args[0][0] == "p2p", f"Expected chat_type='p2p', got {call_args[0][0]}"
        assert call_args[0][1] == "ou_p2p_sender", f"Expected open_id='ou_p2p_sender', got {call_args[0][1]}"

    @pytest.mark.asyncio
    async def test_handle_message_group_session_key_is_chat_id(self):
        """For group messages, get_session_key is called with chat_type='group' and message.chat_id."""
        import types
        from src.handler import handle_message

        mention_id = types.SimpleNamespace(open_id="ou_bot_001")
        mention = types.SimpleNamespace(id=mention_id, key="@_user_1", name="AI 助手")
        msg = make_event_message(
            chat_type="group",
            chat_id="chat_group_999",
            message_type="text",
            content='{"text": "hello @_user_1"}',
            mentions=[mention],
        )
        event_data = make_event_data(message=msg, sender_open_id="ou_group_member")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        mock_session = MagicMock()
        mock_session.name_cache = {}
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=mock_session)
        session_manager.semaphore = MagicMock()
        config = MagicMock()
        config.claude_timeout = 120.0

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock, return_value="reply_001"),
            patch("src.handler.single_turn_worker", new_callable=AsyncMock),
            patch("src.handler.get_display_name", new_callable=AsyncMock, return_value="Alice"),
            patch("asyncio.create_task"),
            patch("src.handler.get_session_key", wraps=__import__("src.session", fromlist=["get_session_key"]).get_session_key) as mock_gsk,
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)

        mock_gsk.assert_called_once()
        call_args = mock_gsk.call_args
        assert call_args[0][0] == "group", f"Expected chat_type='group', got {call_args[0][0]}"
        assert call_args[0][2] == "chat_group_999", f"Expected chat_id='chat_group_999', got {call_args[0][2]}"

    @pytest.mark.asyncio
    async def test_handle_message_group_fetches_display_name(self):
        """For group messages, get_display_name is called with sender's open_id."""
        import types
        from src.handler import handle_message

        mention_id = types.SimpleNamespace(open_id="ou_bot_001")
        mention = types.SimpleNamespace(id=mention_id, key="@_user_1", name="AI 助手")
        msg = make_event_message(
            chat_type="group",
            message_type="text",
            content='{"text": "hello @_user_1"}',
            mentions=[mention],
        )
        event_data = make_event_data(message=msg, sender_open_id="ou_group_sender_555")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        mock_session = MagicMock()
        mock_session.name_cache = {}
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=mock_session)
        session_manager.semaphore = MagicMock()
        config = MagicMock()
        config.claude_timeout = 120.0

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock, return_value="reply_001"),
            patch("src.handler.single_turn_worker", new_callable=AsyncMock),
            patch("src.handler.get_display_name", new_callable=AsyncMock, return_value="Alice") as mock_gdn,
            patch("asyncio.create_task"),
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)

        mock_gdn.assert_called_once()
        call_args = mock_gdn.call_args
        assert call_args[0][1] == "ou_group_sender_555", f"Expected open_id='ou_group_sender_555', got {call_args[0][1]}"

    @pytest.mark.asyncio
    async def test_handle_message_group_prompt_has_prefix(self):
        """For group messages, the prompt passed to worker has [display_name]: prefix (D-14)."""
        import types
        from src.handler import handle_message

        mention_id = types.SimpleNamespace(open_id="ou_bot_001")
        mention = types.SimpleNamespace(id=mention_id, key="@_user_1", name="AI 助手")
        msg = make_event_message(
            chat_type="group",
            message_type="text",
            content='{"text": "help me @_user_1"}',
            mentions=[mention],
        )
        event_data = make_event_data(message=msg)

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        mock_session = MagicMock()
        mock_session.name_cache = {}
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=mock_session)
        session_manager.semaphore = MagicMock()
        config = MagicMock()
        config.claude_timeout = 120.0

        captured_prompts = []

        def capture_create_task(coro):
            # Inspect the coroutine's arguments to capture the prompt
            captured_prompts.append(coro)
            return MagicMock()

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock, return_value="reply_001"),
            patch("src.handler.single_turn_worker", new_callable=AsyncMock) as mock_worker,
            patch("src.handler.get_display_name", new_callable=AsyncMock, return_value="Bob"),
            patch("src.handler.format_prompt", wraps=__import__("src.session", fromlist=["format_prompt"]).format_prompt) as mock_fp,
            patch("asyncio.create_task", side_effect=capture_create_task),
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)

        # format_prompt was called with group chat type and display_name
        mock_fp.assert_called_once()
        call_args = mock_fp.call_args
        assert call_args[0][1] == "group", f"Expected chat_type='group', got {call_args[0][1]}"
        assert call_args[0][2] == "Bob" or call_args[1].get("display_name") == "Bob", "Expected display_name='Bob'"

    @pytest.mark.asyncio
    async def test_handle_message_p2p_prompt_no_prefix(self):
        """For P2P messages, the prompt is plain text without prefix (D-15)."""
        from src.handler import handle_message

        msg = make_event_message(
            chat_type="p2p",
            message_type="text",
            content='{"text": "hello world"}',
        )
        event_data = make_event_data(message=msg)

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        mock_session = MagicMock()
        mock_session.name_cache = {}
        session_manager = MagicMock()
        session_manager.get_or_create = AsyncMock(return_value=mock_session)
        session_manager.semaphore = MagicMock()
        config = MagicMock()
        config.claude_timeout = 120.0

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock, return_value="reply_001"),
            patch("src.handler.single_turn_worker", new_callable=AsyncMock),
            patch("src.handler.format_prompt", wraps=__import__("src.session", fromlist=["format_prompt"]).format_prompt) as mock_fp,
            patch("asyncio.create_task"),
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)

        mock_fp.assert_called_once()
        call_args = mock_fp.call_args
        assert call_args[0][1] == "p2p", f"Expected chat_type='p2p', got {call_args[0][1]}"


class TestNewCommand:
    """Tests for /new command handling."""

    @pytest.mark.asyncio
    async def test_new_command_destroys_session(self):
        """When text is '/new', session_manager.destroy() is called (SESS-03, D-17)."""
        from src.handler import handle_message

        msg = make_event_message(
            chat_type="p2p",
            message_type="text",
            content='{"text": "/new"}',
            message_id="msg_new_001",
        )
        event_data = make_event_data(message=msg, sender_open_id="ou_user_new")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        session_manager = MagicMock()
        session_manager.destroy = AsyncMock()
        config = MagicMock()

        # Mock the areply to avoid real API calls
        mock_reply_resp = MagicMock()
        mock_reply_resp.success.return_value = True
        api_client.im.v1.message.areply = AsyncMock(return_value=mock_reply_resp)

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)
            mock_card.assert_not_called()

        session_manager.destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_command_does_not_call_claude(self):
        """When text is '/new', no Claude worker task is created."""
        from src.handler import handle_message

        msg = make_event_message(
            chat_type="p2p",
            message_type="text",
            content='{"text": "/new"}',
            message_id="msg_new_002",
        )
        event_data = make_event_data(message=msg, sender_open_id="ou_user_new2")

        api_client = MagicMock()
        dedup_cache = MagicMock()
        dedup_cache.is_duplicate.return_value = False

        session_manager = MagicMock()
        session_manager.destroy = AsyncMock()
        config = MagicMock()

        mock_reply_resp = MagicMock()
        mock_reply_resp.success.return_value = True
        api_client.im.v1.message.areply = AsyncMock(return_value=mock_reply_resp)

        with (
            patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card,
            patch("asyncio.create_task") as mock_task,
        ):
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)
            mock_card.assert_not_called()
            mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_command_case_insensitive(self):
        """'/NEW' and ' /new ' are treated as the /new command."""
        from src.handler import handle_message

        for text in ["/NEW", " /new ", "/New"]:
            msg = make_event_message(
                chat_type="p2p",
                message_type="text",
                content=f'{{"text": "{text}"}}',
                message_id=f"msg_new_{text[:3]}",
            )
            event_data = make_event_data(message=msg, sender_open_id="ou_user_new_ci")

            api_client = MagicMock()
            dedup_cache = MagicMock()
            dedup_cache.is_duplicate.return_value = False

            session_manager = MagicMock()
            session_manager.destroy = AsyncMock()
            config = MagicMock()

            mock_reply_resp = MagicMock()
            mock_reply_resp.success.return_value = True
            api_client.im.v1.message.areply = AsyncMock(return_value=mock_reply_resp)

            with patch("src.handler.send_thinking_card", new_callable=AsyncMock):
                await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)

            session_manager.destroy.assert_called_once(), f"/new command '{text}' should trigger destroy"


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
        session_manager = MagicMock()
        config = MagicMock()

        call_order = []
        dedup_cache.is_duplicate.side_effect = lambda x: (call_order.append("dedup"), True)[1]

        with patch("src.handler.send_thinking_card", new_callable=AsyncMock) as mock_card:
            mock_card.side_effect = lambda *a, **kw: call_order.append("card")
            await handle_message(event_data, api_client, "ou_bot_001", dedup_cache, session_manager, config)

        assert "dedup" in call_order
        assert "card" not in call_order


class TestMainImportable:
    """Tests that main.py is importable and has a main() function."""

    def test_main_module_has_main_function(self):
        """main.py must be importable and expose a main() function."""
        import sys
        import os

        assert os.path.exists("main.py"), "main.py must exist"

        with open("main.py") as f:
            content = f.read()
        assert "def main(" in content, "main.py must have a main() function"
        assert "if __name__" in content, "main.py must have __main__ guard"
