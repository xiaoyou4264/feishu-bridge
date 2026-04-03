"""Tests for CardStreamingManager (PUT element content approach)."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestPutContent:
    @pytest.mark.asyncio
    async def test_put_content_calls_put_with_correct_url(self):
        mgr, mock_client = _make_manager()
        await mgr._put_content("hello")
        mock_client.put.assert_called_once()
        url = mock_client.put.call_args[0][0]
        assert "cards/card_123/elements/md_stream/content" in url

    @pytest.mark.asyncio
    async def test_put_content_increments_sequence(self):
        mgr, mock_client = _make_manager()
        await mgr._put_content("first")
        await mgr._put_content("second")
        assert mgr._sequence == 2

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
        assert "✅" in mgr._tool_blocks[-1]

    @pytest.mark.asyncio
    async def test_append_tool_result_error(self):
        mgr, _ = _make_manager()
        await mgr.append_tool_use("Bash", {"command": "fail"})
        await mgr.append_tool_result("error msg", is_error=True)
        assert "❌" in mgr._tool_blocks[-1]

    @pytest.mark.asyncio
    async def test_tool_blocks_in_display_text(self):
        mgr, _ = _make_manager()
        await mgr.append_tool_use("Bash", {"command": "ls"})
        text = mgr._build_display_text("hello", include_typing=False)
        assert "Bash" in text
        assert "hello" in text


class TestFinalize:
    @pytest.mark.asyncio
    async def test_finalize_puts_final_content(self):
        mgr, mock_client = _make_manager()
        await mgr.finalize("Final answer")
        assert mock_client.put.called
        body = mock_client.put.call_args[1]["json"]
        assert "Final answer" in body["content"]
        assert "完成" in body["content"]

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
        await mgr._update_header_title("新标题")
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
