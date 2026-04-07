"""Tests for CardStreamingManager (dual-element: md_stream + md_timer)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import httpx


def _make_manager():
    """Create a CardStreamingManager with mocked httpx client."""
    from src.card_streaming import CardStreamingManager
    mgr = CardStreamingManager(card_id="card_123", tenant_token="token_abc")
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client.put = AsyncMock(return_value=mock_resp)
    mock_client.patch = AsyncMock(return_value=mock_resp)
    mock_client.aclose = AsyncMock()
    mgr._client = mock_client
    return mgr, mock_client


class TestCardStreamingManagerInit:
    def test_init_sets_attributes(self):
        from src.card_streaming import CardStreamingManager
        mgr = CardStreamingManager(card_id="card_1", tenant_token="tok_1")
        assert mgr.card_id == "card_1"
        assert mgr.tenant_token == "tok_1"
        assert mgr._finalized is False
        assert mgr._sequence == 0
        assert mgr._buffer == []
        assert mgr._tool_blocks == []

    def test_init_has_no_timer_task(self):
        from src.card_streaming import CardStreamingManager
        mgr = CardStreamingManager(card_id="card_1", tenant_token="tok_1")
        assert mgr._timer_task is None
        assert mgr._flush_task is None


class TestPutElement:
    @pytest.mark.asyncio
    async def test_put_content_calls_put_with_correct_url(self):
        mgr, mock_client = _make_manager()
        await mgr._put_content("hello")
        mock_client.put.assert_called_once()
        url = mock_client.put.call_args[0][0]
        assert "cards/card_123/elements/md_stream/content" in url

    @pytest.mark.asyncio
    async def test_put_element_targets_timer_element(self):
        from src.card_streaming import TIMER_ELEMENT_ID
        mgr, mock_client = _make_manager()
        await mgr._put_element(TIMER_ELEMENT_ID, "timer text")
        mock_client.put.assert_called_once()
        url = mock_client.put.call_args[0][0]
        assert "cards/card_123/elements/md_timer/content" in url

    @pytest.mark.asyncio
    async def test_put_content_increments_sequence(self):
        mgr, mock_client = _make_manager()
        await mgr._put_content("first")
        await mgr._put_content("second")
        assert mgr._sequence == 2

    @pytest.mark.asyncio
    async def test_shared_sequence_across_elements(self):
        """Both md_stream and md_timer share the same sequence counter."""
        from src.card_streaming import STREAMING_ELEMENT_ID, TIMER_ELEMENT_ID
        mgr, mock_client = _make_manager()
        await mgr._put_element(STREAMING_ELEMENT_ID, "content")
        await mgr._put_element(TIMER_ELEMENT_ID, "timer")
        await mgr._put_element(STREAMING_ELEMENT_ID, "more content")
        assert mgr._sequence == 3
        # Verify sequences are strictly incrementing
        calls = mock_client.put.call_args_list
        seqs = [c[1]["json"]["sequence"] for c in calls]
        assert seqs == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_put_content_sends_content_and_sequence(self):
        mgr, mock_client = _make_manager()
        await mgr._put_content("test content")
        body = mock_client.put.call_args[1]["json"]
        assert body["content"] == "test content"
        assert body["sequence"] == 1


class TestAppendText:
    @pytest.mark.asyncio
    async def test_append_text_adds_to_buffer(self):
        mgr, _ = _make_manager()
        await mgr.append_text("hello ")
        await mgr.append_text("world")
        assert mgr._buffer == ["hello ", "world"]

    @pytest.mark.asyncio
    async def test_append_text_ignored_when_finalized(self):
        mgr, _ = _make_manager()
        mgr._finalized = True
        await mgr.append_text("should not appear")
        assert mgr._buffer == []


class TestToolRendering:
    @pytest.mark.asyncio
    async def test_append_tool_use_adds_block(self):
        mgr, _ = _make_manager()
        await mgr.append_tool_use("Bash", {"command": "ls"})
        assert len(mgr._tool_blocks) == 1
        assert "Bash" in mgr._tool_blocks[0]

    @pytest.mark.asyncio
    async def test_append_tool_result_updates_last_block(self):
        mgr, _ = _make_manager()
        await mgr.append_tool_use("Read", {"file": "test.py"})
        await mgr.append_tool_result("file contents", is_error=False)
        assert "\u2705" in mgr._tool_blocks[-1]

    @pytest.mark.asyncio
    async def test_append_tool_result_error(self):
        mgr, _ = _make_manager()
        await mgr.append_tool_use("Bash", {"command": "fail"})
        await mgr.append_tool_result("error msg", is_error=True)
        assert "\u274c" in mgr._tool_blocks[-1]

    @pytest.mark.asyncio
    async def test_tool_blocks_in_display_text(self):
        mgr, _ = _make_manager()
        await mgr.append_tool_use("Bash", {"command": "ls"})
        text = mgr._build_display_text("hello", include_typing=False)
        assert "Bash" in text
        assert "hello" in text


class TestBuildDisplayText:
    def test_no_timer_in_display_text(self):
        """Display text should NOT contain timer line or --- divider."""
        mgr, _ = _make_manager()
        # After stream start, typing indicator is "正在输入..." (no "思考" in timer)
        mgr.mark_stream_start()
        text = mgr._build_display_text("hello world", include_typing=True)
        assert "---" not in text
        assert "\u23f1" not in text  # No timer emoji ⏱
        assert "\u00b7" not in text  # No middle dot separator from timer

    def test_thinking_indicator(self):
        """During think phase, should show thinking indicator."""
        mgr, _ = _make_manager()
        text = mgr._build_display_text("", include_typing=True)
        assert "\u601d\u8003\u4e2d" in text  # "思考中"

    def test_typing_indicator_after_stream_start(self):
        """After streaming starts, should show typing indicator."""
        mgr, _ = _make_manager()
        mgr.mark_stream_start()
        text = mgr._build_display_text("some text", include_typing=True)
        assert "\u8f93\u5165" in text  # "输入"
        assert "---" not in text  # Still no timer/divider


class TestStartCreatesMultipleTasks:
    @pytest.mark.asyncio
    async def test_start_creates_both_tasks(self):
        mgr, _ = _make_manager()
        await mgr.start()
        assert mgr._flush_task is not None
        assert mgr._timer_task is not None
        assert not mgr._flush_task.done()
        assert not mgr._timer_task.done()
        # Clean up
        mgr._flush_task.cancel()
        mgr._timer_task.cancel()
        try:
            await mgr._flush_task
        except asyncio.CancelledError:
            pass
        try:
            await mgr._timer_task
        except asyncio.CancelledError:
            pass


class TestFinalize:
    @pytest.mark.asyncio
    async def test_finalize_updates_both_elements(self):
        """Finalize should PUT to both md_stream and md_timer."""
        from src.card_streaming import STREAMING_ELEMENT_ID, TIMER_ELEMENT_ID
        mgr, mock_client = _make_manager()
        await mgr.finalize("Final answer")

        put_urls = [c[0][0] for c in mock_client.put.call_args_list]
        stream_puts = [u for u in put_urls if STREAMING_ELEMENT_ID in u]
        timer_puts = [u for u in put_urls if TIMER_ELEMENT_ID in u]
        assert len(stream_puts) >= 1, "Should PUT to md_stream"
        assert len(timer_puts) >= 1, "Should PUT to md_timer"

    @pytest.mark.asyncio
    async def test_finalize_content_has_no_timer(self):
        """Final content PUT to md_stream should NOT contain timer text."""
        from src.card_streaming import STREAMING_ELEMENT_ID
        mgr, mock_client = _make_manager()
        await mgr.finalize("Final answer")

        # Find the PUT to md_stream
        for call in mock_client.put.call_args_list:
            url = call[0][0]
            if STREAMING_ELEMENT_ID in url:
                body = call[1]["json"]
                assert "\u5b8c\u6210" not in body["content"]  # "完成" should be in timer, not content
                assert "Final answer" in body["content"]
                break

    @pytest.mark.asyncio
    async def test_finalize_timer_shows_completion(self):
        """Final timer PUT should show completion status."""
        from src.card_streaming import TIMER_ELEMENT_ID
        mgr, mock_client = _make_manager()
        await mgr.finalize("Final answer")

        # Find the PUT to md_timer
        for call in mock_client.put.call_args_list:
            url = call[0][0]
            if TIMER_ELEMENT_ID in url:
                body = call[1]["json"]
                assert "\u5b8c\u6210" in body["content"]  # "完成"
                break

    @pytest.mark.asyncio
    async def test_finalize_sets_finalized_flag(self):
        mgr, _ = _make_manager()
        await mgr.finalize("done")
        assert mgr._finalized is True

    @pytest.mark.asyncio
    async def test_finalize_skips_if_already_finalized(self):
        mgr, mock_client = _make_manager()
        mgr._finalized = True
        await mgr.finalize("should not send")
        mock_client.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalize_closes_httpx_client(self):
        mgr, mock_client = _make_manager()
        await mgr.finalize("done")
        mock_client.aclose.assert_awaited_once()


class TestHeaderUpdate:
    @pytest.mark.asyncio
    async def test_update_header_calls_patch(self):
        mgr, mock_client = _make_manager()
        await mgr._update_header_title("\u65b0\u6807\u9898")
        mock_client.patch.assert_called_once()
        url = mock_client.patch.call_args[0][0]
        assert "settings" in url


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_put_content_retries_on_http_error(self):
        mgr, mock_client = _make_manager()
        error_resp = MagicMock()
        error_resp.status_code = 429
        mock_client.put.side_effect = [
            httpx.HTTPStatusError("rate limited", request=MagicMock(), response=error_resp),
            MagicMock(raise_for_status=MagicMock()),
        ]
        await mgr._put_content("test")
        assert mock_client.put.call_count == 2
