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
# single_turn_worker tests
# ---------------------------------------------------------------------------

class TestSingleTurnWorker:
    @pytest.mark.asyncio
    async def test_worker_calls_query_with_prompt(self):
        """single_turn_worker calls client.query() with the formatted prompt."""
        from src.claude_worker import single_turn_worker

        client = make_mock_client()
        session = make_mock_session(client)
        semaphore = asyncio.Semaphore(5)
        api_client = MagicMock()

        with patch("src.claude_worker.update_card_content", new=AsyncMock()):
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
        with patch("src.claude_worker.update_card_content", new=mock_update):
            await single_turn_worker(
                session=session,
                prompt="test prompt",
                reply_message_id="msg_reply_001",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )

        mock_update.assert_awaited_once_with(api_client, "msg_reply_001", "Hello from Claude")

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
        with patch("src.claude_worker.send_error_card", new=mock_error):
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
        with patch("src.claude_worker.send_error_card", new=mock_error):
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
        with patch("src.claude_worker.send_error_card", new=mock_error):
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
        with patch("src.claude_worker.send_error_card", new=mock_error):
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
        with patch("src.claude_worker.send_error_card", new=mock_error):
            # Should still NOT raise
            await single_turn_worker(
                session=session,
                prompt="test",
                reply_message_id="msg_reply_001",
                api_client=api_client,
                semaphore=semaphore,
                timeout=30.0,
            )
