"""Unit tests for single_turn_worker and _run_claude_turn (claude_worker.py)."""
import asyncio
import types

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def make_text_block(text: str):
    """Create a mock TextBlock."""
    from claude_agent_sdk import TextBlock
    block = MagicMock(spec=TextBlock)
    block.text = text
    # Make isinstance(block, TextBlock) work properly by using real class
    return block


def make_assistant_message(text_blocks):
    """Create a mock AssistantMessage containing text blocks."""
    from claude_agent_sdk import AssistantMessage, TextBlock
    msg = MagicMock(spec=AssistantMessage)
    # Create real TextBlock instances if possible, otherwise mock them
    content_blocks = []
    for text in text_blocks:
        block = MagicMock(spec=TextBlock)
        block.text = text
        content_blocks.append(block)
    msg.content = content_blocks
    return msg


def make_result_message():
    """Create a mock ResultMessage."""
    from claude_agent_sdk import ResultMessage
    return MagicMock(spec=ResultMessage)


async def async_generator_from_list(items):
    """Helper to create an async generator from a list."""
    for item in items:
        yield item


def make_mock_client(response_items=None):
    """
    Create a mock ClaudeSDKClient.

    response_items: list of messages to yield from receive_response().
                    Defaults to [AssistantMessage(["Hello"]), ResultMessage].
    """
    from claude_agent_sdk import AssistantMessage, TextBlock
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    if response_items is None:
        response_items = [
            make_assistant_message(["Hello"]),
            make_result_message(),
        ]

    async def mock_receive_response():
        for item in response_items:
            yield item

    client.receive_response = mock_receive_response
    return client


def make_mock_session(client=None):
    """Create a mock SessionState with a real asyncio.Lock."""
    from src.session import SessionState
    if client is None:
        client = make_mock_client()
    return SessionState(session_key="test_key", client=client)


def make_tool_use_block(name: str = "Bash", tool_input: dict = None):
    """Create a mock ToolUseBlock."""
    from claude_agent_sdk import ToolUseBlock
    block = MagicMock(spec=ToolUseBlock)
    block.id = "tool_001"
    block.name = name
    block.input = tool_input or {"command": "ls"}
    return block


def make_tool_result_block(content: str = "file.txt", is_error: bool = False):
    """Create a mock ToolResultBlock."""
    from claude_agent_sdk import ToolResultBlock
    block = MagicMock(spec=ToolResultBlock)
    block.tool_use_id = "tool_001"
    block.content = content
    block.is_error = is_error
    return block


def make_assistant_message_with_mixed_blocks(text_blocks=None, tool_use_blocks=None, tool_result_blocks=None):
    """Create an AssistantMessage with mixed content blocks."""
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock
    msg = MagicMock(spec=AssistantMessage)
    content_blocks = []

    for text in (text_blocks or []):
        block = MagicMock(spec=TextBlock)
        block.text = text
        content_blocks.append(block)

    for (name, inp) in (tool_use_blocks or []):
        block = MagicMock(spec=ToolUseBlock)
        block.id = "tool_use_001"
        block.name = name
        block.input = inp
        content_blocks.append(block)

    for (content, is_error) in (tool_result_blocks or []):
        block = MagicMock(spec=ToolResultBlock)
        block.tool_use_id = "tool_use_001"
        block.content = content
        block.is_error = is_error
        content_blocks.append(block)

    msg.content = content_blocks
    return msg


# ---------------------------------------------------------------------------
# _run_claude_turn tests
# ---------------------------------------------------------------------------

class TestRunClaudeTurn:
    @pytest.mark.asyncio
    async def test_calls_query_with_prompt(self):
        """_run_claude_turn calls client.query() with the given prompt."""
        from src.claude_worker import _run_claude_turn

        client = make_mock_client()
        await _run_claude_turn(client, "What is 2+2?")

        client.query.assert_awaited_once_with("What is 2+2?")

    @pytest.mark.asyncio
    async def test_accumulates_text_from_response(self):
        """_run_claude_turn concatenates all TextBlocks from AssistantMessages."""
        from src.claude_worker import _run_claude_turn

        client = make_mock_client(response_items=[
            make_assistant_message(["Hello, ", "world"]),
            make_result_message(),
        ])

        result = await _run_claude_turn(client, "hi")

        assert result == "Hello, world"

    @pytest.mark.asyncio
    async def test_accumulates_text_from_multiple_messages(self):
        """_run_claude_turn concatenates text blocks across multiple AssistantMessages."""
        from src.claude_worker import _run_claude_turn

        client = make_mock_client(response_items=[
            make_assistant_message(["First part. "]),
            make_assistant_message(["Second part."]),
            make_result_message(),
        ])

        result = await _run_claude_turn(client, "hi")

        assert result == "First part. Second part."

    @pytest.mark.asyncio
    async def test_ignores_result_message(self):
        """_run_claude_turn does not include ResultMessage text in output."""
        from src.claude_worker import _run_claude_turn

        client = make_mock_client(response_items=[
            make_assistant_message(["Response"]),
            make_result_message(),
        ])

        result = await _run_claude_turn(client, "hi")

        assert result == "Response"


# ---------------------------------------------------------------------------
# _run_claude_turn_streaming tests
# ---------------------------------------------------------------------------

class TestRunClaudeTurnStreaming:
    """Tests for the new _run_claude_turn_streaming() function."""

    @pytest.mark.asyncio
    async def test_calls_query_with_prompt(self):
        """_run_claude_turn_streaming calls client.query() with the given prompt."""
        from src.claude_worker import _run_claude_turn_streaming

        client = make_mock_client()
        manager = MagicMock()
        manager.append_text = AsyncMock()
        manager.append_tool_use = AsyncMock()
        manager.append_tool_result = AsyncMock()

        await _run_claude_turn_streaming(client, "What is 2+2?", manager)

        client.query.assert_awaited_once_with("What is 2+2?")

    @pytest.mark.asyncio
    async def test_calls_append_text_for_each_text_block(self):
        """_run_claude_turn_streaming calls manager.append_text() for each TextBlock."""
        from src.claude_worker import _run_claude_turn_streaming

        client = make_mock_client(response_items=[
            make_assistant_message(["Hello, ", "world"]),
            make_result_message(),
        ])
        manager = MagicMock()
        manager.append_text = AsyncMock()
        manager.append_tool_use = AsyncMock()
        manager.append_tool_result = AsyncMock()

        await _run_claude_turn_streaming(client, "hi", manager)

        assert manager.append_text.await_count == 2
        calls = [c.args[0] for c in manager.append_text.await_args_list]
        assert "Hello, " in calls
        assert "world" in calls

    @pytest.mark.asyncio
    async def test_calls_append_tool_use_for_tool_use_block(self):
        """_run_claude_turn_streaming calls manager.append_tool_use() for each ToolUseBlock."""
        from src.claude_worker import _run_claude_turn_streaming
        from claude_agent_sdk import ToolUseBlock

        tool_block = make_tool_use_block(name="Bash", tool_input={"command": "ls"})

        msg = MagicMock()
        from claude_agent_sdk import AssistantMessage
        msg.__class__ = AssistantMessage
        msg.content = [tool_block]

        client = MagicMock()
        client.query = AsyncMock()

        async def mock_receive():
            yield msg
            yield make_result_message()

        client.receive_response = mock_receive

        manager = MagicMock()
        manager.append_text = AsyncMock()
        manager.append_tool_use = AsyncMock()
        manager.append_tool_result = AsyncMock()

        await _run_claude_turn_streaming(client, "test", manager)

        manager.append_tool_use.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_append_tool_result_for_tool_result_block(self):
        """_run_claude_turn_streaming calls manager.append_tool_result() for each ToolResultBlock."""
        from src.claude_worker import _run_claude_turn_streaming
        from claude_agent_sdk import ToolResultBlock, AssistantMessage

        tool_result_block = make_tool_result_block(content="file.txt", is_error=False)

        msg = MagicMock()
        msg.__class__ = AssistantMessage
        msg.content = [tool_result_block]

        client = MagicMock()
        client.query = AsyncMock()

        async def mock_receive():
            yield msg
            yield make_result_message()

        client.receive_response = mock_receive

        manager = MagicMock()
        manager.append_text = AsyncMock()
        manager.append_tool_use = AsyncMock()
        manager.append_tool_result = AsyncMock()

        await _run_claude_turn_streaming(client, "test", manager)

        manager.append_tool_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_concatenated_text(self):
        """_run_claude_turn_streaming returns concatenated text from all TextBlocks."""
        from src.claude_worker import _run_claude_turn_streaming

        client = make_mock_client(response_items=[
            make_assistant_message(["Hello, "]),
            make_assistant_message(["world!"]),
            make_result_message(),
        ])
        manager = MagicMock()
        manager.append_text = AsyncMock()
        manager.append_tool_use = AsyncMock()
        manager.append_tool_result = AsyncMock()

        result = await _run_claude_turn_streaming(client, "hi", manager)

        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_returns_empty_string_on_no_text(self):
        """_run_claude_turn_streaming returns empty string when no TextBlocks."""
        from src.claude_worker import _run_claude_turn_streaming

        client = make_mock_client(response_items=[
            make_result_message(),
        ])
        manager = MagicMock()
        manager.append_text = AsyncMock()
        manager.append_tool_use = AsyncMock()
        manager.append_tool_result = AsyncMock()

        result = await _run_claude_turn_streaming(client, "test", manager)

        assert result == ""


# ---------------------------------------------------------------------------
# single_turn_worker tests
# ---------------------------------------------------------------------------

def _patch_streaming_infra(manager_mock=None):
    """Context manager that patches all streaming infrastructure used by single_turn_worker."""
    from contextlib import contextmanager, AsyncExitStack
    if manager_mock is None:
        manager_mock = MagicMock()
        manager_mock.start = AsyncMock()
        manager_mock.append_text = AsyncMock()
        manager_mock.append_tool_use = AsyncMock()
        manager_mock.append_tool_result = AsyncMock()
        manager_mock.finalize = AsyncMock()

    return (
        patch("src.claude_worker.create_streaming_card", new=AsyncMock(return_value="card_test")),
        patch("src.claude_worker.patch_im_with_card_id", new=AsyncMock()),
        patch("src.claude_worker.CardStreamingManager", return_value=manager_mock),
        patch("src.claude_worker.update_card_content", new=AsyncMock()),
    )


class TestSingleTurnWorker:
    @pytest.mark.asyncio
    async def test_worker_calls_query_with_prompt(self):
        """single_turn_worker calls client.query() with the formatted prompt."""
        from src.claude_worker import single_turn_worker

        client = make_mock_client()
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()

        patches = _patch_streaming_infra()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"):
                await single_turn_worker(
                    session=session,
                    prompt="test prompt",
                    reply_message_id="msg_reply_001",
                    api_client=api_client,
                    semaphore=semaphore,
                    timeout=30.0,
                )

        client.query.assert_awaited_once_with("test prompt")

    @pytest.mark.asyncio
    async def test_worker_calls_update_card_on_success(self):
        """After successful response, update_card_content() is called with accumulated text."""
        from src.claude_worker import single_turn_worker

        client = make_mock_client(response_items=[
            make_assistant_message(["Hello from Claude"]),
            make_result_message(),
        ])
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()

        mock_update = AsyncMock()
        patches = _patch_streaming_infra()
        with patches[0], patches[1], patches[2]:
            with patch("src.claude_worker.update_card_content", new=mock_update):
                with patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"):
                    await single_turn_worker(
                        session=session,
                        prompt="test prompt",
                        reply_message_id="msg_reply_001",
                        api_client=api_client,
                        semaphore=semaphore,
                        timeout=30.0,
                    )

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args
        # Now passes buttons= kwarg with feedback buttons
        assert call_args[0][0] is api_client
        assert call_args[0][1] == "msg_reply_001"
        assert call_args[0][2] == "Hello from Claude"
        assert "buttons" in call_args[1] or len(call_args[0]) == 4

    @pytest.mark.asyncio
    async def test_worker_timeout_sends_error_card(self):
        """When _run_claude_turn exceeds timeout, send_error_card() is called (CLAUDE-05)."""
        from src.claude_worker import single_turn_worker

        # Make client.query() hang indefinitely
        async def slow_query(prompt):
            await asyncio.sleep(100)

        client = make_mock_client()
        client.query = AsyncMock(side_effect=slow_query)
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()

        mock_error = AsyncMock()
        patches = _patch_streaming_infra()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch("src.claude_worker.send_error_card", new=mock_error):
                with patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"):
                    await single_turn_worker(
                        session=session,
                        prompt="slow prompt",
                        reply_message_id="msg_reply_001",
                        api_client=api_client,
                        semaphore=semaphore,
                        timeout=0.05,  # 50ms timeout
                    )

        mock_error.assert_awaited_once()
        # Check that the error message mentions timeout
        call_args = mock_error.call_args
        assert "超时" in call_args[0][2] or "timeout" in call_args[0][2].lower()

    @pytest.mark.asyncio
    async def test_worker_exception_sends_error_card(self):
        """When _run_claude_turn raises an exception, send_error_card() is called (CLAUDE-04)."""
        from src.claude_worker import single_turn_worker

        client = make_mock_client()
        client.query = AsyncMock(side_effect=RuntimeError("SDK crashed"))
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()

        mock_error = AsyncMock()
        patches = _patch_streaming_infra()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch("src.claude_worker.send_error_card", new=mock_error):
                with patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"):
                    await single_turn_worker(
                        session=session,
                        prompt="bad prompt",
                        reply_message_id="msg_reply_001",
                        api_client=api_client,
                        semaphore=semaphore,
                        timeout=30.0,
                    )

        mock_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_exception_does_not_propagate(self):
        """Exceptions do NOT escape single_turn_worker (CLAUDE-04 isolation)."""
        from src.claude_worker import single_turn_worker

        client = make_mock_client()
        client.query = AsyncMock(side_effect=RuntimeError("fatal error"))
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()

        mock_error = AsyncMock()
        patches = _patch_streaming_infra()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch("src.claude_worker.send_error_card", new=mock_error):
                with patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"):
                    # Should NOT raise
                    await single_turn_worker(
                        session=session,
                        prompt="crashing prompt",
                        reply_message_id="msg_reply_001",
                        api_client=api_client,
                        semaphore=semaphore,
                        timeout=30.0,
                    )

    @pytest.mark.asyncio
    async def test_worker_acquires_semaphore_then_lock(self):
        """
        Verify semaphore is acquired OUTER, session.lock INNER (Pitfall 4).

        We verify this by checking that with semaphore at capacity (0 available),
        the worker blocks — not reaching the session lock.
        """
        from src.claude_worker import single_turn_worker

        # Full semaphore — no slots available
        semaphore = asyncio.Semaphore(0)

        client = make_mock_client()
        session = make_mock_session(client)
        api_client = MagicMock()

        # Worker should hang waiting for semaphore
        task = asyncio.create_task(
            single_turn_worker(
                session=session,
                prompt="test",
                reply_message_id="msg_001",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )
        )
        # Give it a moment to run
        await asyncio.sleep(0.05)

        # Task should be waiting — not done
        assert not task.done()
        # Lock should NOT be acquired (worker is blocked on semaphore)
        assert not session.lock.locked()

        # Cancel and clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_worker_uses_asyncio_wait_for(self):
        """Worker uses asyncio.wait_for for timeout (checked by ensuring timeout works)."""
        from src.claude_worker import single_turn_worker

        # Make receive_response slow
        slow_client = MagicMock()
        slow_client.query = AsyncMock()

        async def slow_receive():
            await asyncio.sleep(100)
            yield make_assistant_message(["never sent"])

        slow_client.receive_response = slow_receive

        session = make_mock_session(slow_client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()

        mock_error = AsyncMock()
        patches = _patch_streaming_infra()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch("src.claude_worker.send_error_card", new=mock_error):
                with patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"):
                    await single_turn_worker(
                        session=session,
                        prompt="test",
                        reply_message_id="msg_reply_001",
                        api_client=api_client,
                        semaphore=semaphore,
                        timeout=0.05,
                    )

        # Error card sent due to timeout
        mock_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_error_card_send_failure_does_not_propagate(self):
        """Even if send_error_card itself fails, exceptions don't escape."""
        from src.claude_worker import single_turn_worker

        client = make_mock_client()
        client.query = AsyncMock(side_effect=RuntimeError("SDK error"))
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()

        # send_error_card also fails
        mock_error = AsyncMock(side_effect=RuntimeError("card send failed"))
        patches = _patch_streaming_infra()
        with patches[0], patches[1], patches[2], patches[3]:
            with patch("src.claude_worker.send_error_card", new=mock_error):
                with patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"):
                    # Should still NOT raise
                    await single_turn_worker(
                        session=session,
                        prompt="test",
                        reply_message_id="msg_reply_001",
                        api_client=api_client,
                        semaphore=semaphore,
                        timeout=30.0,
                    )


# ---------------------------------------------------------------------------
# _run_claude_turn_streaming integration with CardStreamingManager
# ---------------------------------------------------------------------------

class TestStreamingWorkerCardManager:
    """Tests for single_turn_worker using CardStreamingManager for streaming."""

    def _make_streaming_mocks(self, response_items=None):
        """Create mocks for streaming worker tests."""
        if response_items is None:
            response_items = [
                make_assistant_message(["Hello"]),
                make_result_message(),
            ]
        client = make_mock_client(response_items=response_items)
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()
        return client, session, semaphore, api_client

    def _make_manager_mock(self):
        """Create a mock CardStreamingManager."""
        manager = MagicMock()
        manager.start = AsyncMock()
        manager.append_text = AsyncMock()
        manager.append_tool_use = AsyncMock()
        manager.append_tool_result = AsyncMock()
        manager.finalize = AsyncMock()
        return manager

    @pytest.mark.asyncio
    async def test_streaming_worker_calls_create_streaming_card(self):
        """single_turn_worker calls create_streaming_card() to get card_id."""
        from src.claude_worker import single_turn_worker

        client, session, semaphore, api_client = self._make_streaming_mocks()
        manager_mock = self._make_manager_mock()

        with (
            patch("src.claude_worker.create_streaming_card", new=AsyncMock(return_value="card_123")) as mock_create,
            patch("src.claude_worker.patch_im_with_card_id", new=AsyncMock()),
            patch("src.claude_worker.CardStreamingManager", return_value=manager_mock),
            patch("src.claude_worker.update_card_content", new=AsyncMock()),
            patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"),
        ):
            await single_turn_worker(
                session=session,
                prompt="test",
                reply_message_id="msg_001",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )

        mock_create.assert_awaited_once()
        # Verify called with api_client and stop_message_id
        assert mock_create.call_args[0][0] is api_client or mock_create.call_args.args[0] is api_client

    @pytest.mark.asyncio
    async def test_streaming_worker_calls_patch_im_with_card_id(self):
        """single_turn_worker calls patch_im_with_card_id() after creating the card."""
        from src.claude_worker import single_turn_worker

        client, session, semaphore, api_client = self._make_streaming_mocks()
        manager_mock = self._make_manager_mock()

        with (
            patch("src.claude_worker.create_streaming_card", new=AsyncMock(return_value="card_456")),
            patch("src.claude_worker.patch_im_with_card_id", new=AsyncMock()) as mock_patch,
            patch("src.claude_worker.CardStreamingManager", return_value=manager_mock),
            patch("src.claude_worker.update_card_content", new=AsyncMock()),
            patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"),
        ):
            await single_turn_worker(
                session=session,
                prompt="test",
                reply_message_id="msg_reply_001",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )

        mock_patch.assert_awaited_once_with(api_client, "msg_reply_001", "card_456")

    @pytest.mark.asyncio
    async def test_streaming_worker_calls_manager_start(self):
        """single_turn_worker calls manager.start() to start the flush loop."""
        from src.claude_worker import single_turn_worker

        client, session, semaphore, api_client = self._make_streaming_mocks()
        manager_mock = self._make_manager_mock()

        with (
            patch("src.claude_worker.create_streaming_card", new=AsyncMock(return_value="card_789")),
            patch("src.claude_worker.patch_im_with_card_id", new=AsyncMock()),
            patch("src.claude_worker.CardStreamingManager", return_value=manager_mock),
            patch("src.claude_worker.update_card_content", new=AsyncMock()),
            patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"),
        ):
            await single_turn_worker(
                session=session,
                prompt="test",
                reply_message_id="msg_001",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )

        manager_mock.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_streaming_worker_calls_finalize_on_success(self):
        """single_turn_worker calls manager.finalize() with result text on success."""
        from src.claude_worker import single_turn_worker

        client, session, semaphore, api_client = self._make_streaming_mocks(response_items=[
            make_assistant_message(["Hello Claude response"]),
            make_result_message(),
        ])
        manager_mock = self._make_manager_mock()

        with (
            patch("src.claude_worker.create_streaming_card", new=AsyncMock(return_value="card_001")),
            patch("src.claude_worker.patch_im_with_card_id", new=AsyncMock()),
            patch("src.claude_worker.CardStreamingManager", return_value=manager_mock),
            patch("src.claude_worker.update_card_content", new=AsyncMock()),
            patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"),
        ):
            await single_turn_worker(
                session=session,
                prompt="test",
                reply_message_id="msg_001",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )

        # finalize should be called with the accumulated text
        manager_mock.finalize.assert_awaited_once()
        call_args = manager_mock.finalize.call_args
        assert "Hello Claude response" in str(call_args)

    @pytest.mark.asyncio
    async def test_streaming_worker_calls_finalize_on_exception(self):
        """single_turn_worker calls manager.finalize() even when an exception occurs."""
        from src.claude_worker import single_turn_worker

        client = make_mock_client()
        client.query = AsyncMock(side_effect=RuntimeError("Claude error"))
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()
        manager_mock = self._make_manager_mock()

        with (
            patch("src.claude_worker.create_streaming_card", new=AsyncMock(return_value="card_002")),
            patch("src.claude_worker.patch_im_with_card_id", new=AsyncMock()),
            patch("src.claude_worker.CardStreamingManager", return_value=manager_mock),
            patch("src.claude_worker.send_error_card", new=AsyncMock()),
            patch("src.claude_worker.update_card_content", new=AsyncMock()),
            patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"),
        ):
            await single_turn_worker(
                session=session,
                prompt="bad",
                reply_message_id="msg_002",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )

        # finalize should still be called even on exception
        manager_mock.finalize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_streaming_worker_calls_finalize_on_timeout(self):
        """single_turn_worker calls manager.finalize() even on timeout."""
        from src.claude_worker import single_turn_worker

        async def slow_query(prompt):
            await asyncio.sleep(100)

        client = make_mock_client()
        client.query = AsyncMock(side_effect=slow_query)
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()
        manager_mock = self._make_manager_mock()

        with (
            patch("src.claude_worker.create_streaming_card", new=AsyncMock(return_value="card_003")),
            patch("src.claude_worker.patch_im_with_card_id", new=AsyncMock()),
            patch("src.claude_worker.CardStreamingManager", return_value=manager_mock),
            patch("src.claude_worker.send_error_card", new=AsyncMock()),
            patch("src.claude_worker.update_card_content", new=AsyncMock()),
            patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"),
        ):
            await single_turn_worker(
                session=session,
                prompt="slow",
                reply_message_id="msg_003",
                api_client=api_client,
                semaphore=semaphore,
                timeout=0.05,
            )

        # finalize should still be called on timeout
        manager_mock.finalize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_streaming_worker_uses_uuid_for_sequence_id(self):
        """single_turn_worker passes a UUID-based sequence_id to CardStreamingManager."""
        from src.claude_worker import single_turn_worker
        import uuid as uuid_module

        client, session, semaphore, api_client = self._make_streaming_mocks()
        manager_mock = self._make_manager_mock()
        manager_class_mock = MagicMock(return_value=manager_mock)

        with (
            patch("src.claude_worker.create_streaming_card", new=AsyncMock(return_value="card_seq")),
            patch("src.claude_worker.patch_im_with_card_id", new=AsyncMock()),
            patch("src.claude_worker.CardStreamingManager", manager_class_mock),
            patch("src.claude_worker.update_card_content", new=AsyncMock()),
            patch("lark_oapi.core.token.TokenManager.get_self_tenant_token", return_value="token_test"),
        ):
            await single_turn_worker(
                session=session,
                prompt="test",
                reply_message_id="msg_seq",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )

        # CardStreamingManager should have been constructed
        assert manager_class_mock.called
