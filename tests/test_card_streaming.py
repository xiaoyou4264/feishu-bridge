"""Tests for CardStreamingManager."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.card_streaming import CardStreamingManager


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient for CardKit API calls."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock()
    client.patch = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def manager(mock_httpx_client):
    """Create CardStreamingManager with mocked httpx client."""
    mgr = CardStreamingManager(
        card_id="test_card_123",
        tenant_token="test_token",
        flush_interval=0.1,  # Fast flush for tests
    )
    mgr._client = mock_httpx_client
    return mgr


class TestCardStreamingManagerInit:
    """Test CardStreamingManager initialization."""

    def test_init_sets_attributes(self):
        mgr = CardStreamingManager(
            card_id="card_abc",
            tenant_token="token_xyz",
            flush_interval=0.5,
        )
        assert mgr.card_id == "card_abc"
        assert mgr.tenant_token == "token_xyz"
        assert mgr.flush_interval == 0.5
        assert mgr._buffer == []
        assert mgr._tool_blocks == []
        assert mgr._sequence_created is False
        assert mgr._finalized is False


class TestSequenceLifecycle:
    """Test sequence create/update/finish lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_sequence(self, manager, mock_httpx_client):
        """start() creates sequence via POST."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_resp

        await manager.start()

        # Verify POST call
        mock_httpx_client.post.assert_called_once()
        call_args = mock_httpx_client.post.call_args
        assert "/cards/test_card_123/sequences" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer test_token"
        assert "sequence_id" in call_args[1]["json"]
        assert manager._sequence_created is True

        # Cleanup
        await manager.finalize("")

    @pytest.mark.asyncio
    async def test_flush_buffer_patches_sequence(self, manager, mock_httpx_client):
        """_flush_buffer() sends PATCH with accumulated content."""
        manager._sequence_created = True
        manager._buffer = ["Hello", " ", "world"]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx_client.patch.return_value = mock_resp

        await manager._flush_buffer()

        # Verify PATCH call
        mock_httpx_client.patch.assert_called_once()
        call_args = mock_httpx_client.patch.call_args
        assert f"/cards/test_card_123/sequences/{manager.sequence_id}" in call_args[0][0]
        body = call_args[1]["json"]
        content = body["content"]["body"]["elements"][0]["content"]
        assert "Hello world" in content
        assert "_正在输入..._" in content

        # Buffer cleared after flush
        assert manager._buffer == []

    @pytest.mark.asyncio
    async def test_finish_sequence_sends_done_true(self, manager, mock_httpx_client):
        """_finish_sequence() sends PATCH with done=true."""
        manager._sequence_created = True

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx_client.patch.return_value = mock_resp

        await manager._finish_sequence("Final text")

        # Verify PATCH call with done=true
        mock_httpx_client.patch.assert_called_once()
        call_args = mock_httpx_client.patch.call_args
        body = call_args[1]["json"]
        assert body["done"] is True
        content = body["content"]["body"]["elements"][0]["content"]
        assert "Final text" in content
        assert "_正在输入..._" not in content


class TestTextAccumulation:
    """Test text token accumulation and batching."""

    @pytest.mark.asyncio
    async def test_append_text_accumulates_in_buffer(self, manager):
        """append_text() adds tokens to buffer."""
        await manager.append_text("Hello")
        await manager.append_text(" ")
        await manager.append_text("world")

        assert manager._buffer == ["Hello", " ", "world"]

    @pytest.mark.asyncio
    async def test_append_text_after_finalize_ignored(self, manager, mock_httpx_client):
        """append_text() after finalize() is ignored."""
        manager._finalized = True
        await manager.append_text("ignored")

        assert manager._buffer == []


class TestToolRendering:
    """Test tool use/result block rendering."""

    @pytest.mark.asyncio
    async def test_append_tool_use_creates_markdown_block(self, manager):
        """append_tool_use() creates markdown tool block."""
        await manager.append_tool_use("Bash", {"command": "ls -la"})

        assert len(manager._tool_blocks) == 1
        block = manager._tool_blocks[0]
        assert "🔧 Bash" in block
        assert "ls -la" in block

    @pytest.mark.asyncio
    async def test_append_tool_result_success(self, manager):
        """append_tool_result() creates success block."""
        await manager.append_tool_result("file1.txt\nfile2.txt", is_error=False)

        assert len(manager._tool_blocks) == 1
        block = manager._tool_blocks[0]
        assert "✅ Done" in block
        assert "file1.txt" in block

    @pytest.mark.asyncio
    async def test_append_tool_result_error(self, manager):
        """append_tool_result() creates error block."""
        await manager.append_tool_result("Permission denied", is_error=True)

        assert len(manager._tool_blocks) == 1
        block = manager._tool_blocks[0]
        assert "❌ Error" in block
        assert "Permission denied" in block

    @pytest.mark.asyncio
    async def test_tool_blocks_included_in_flush(self, manager, mock_httpx_client):
        """Tool blocks are included in PATCH content."""
        manager._sequence_created = True
        await manager.append_tool_use("Read", {"file": "test.py"})
        manager._buffer = ["Some text"]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx_client.patch.return_value = mock_resp

        await manager._flush_buffer()

        call_args = mock_httpx_client.patch.call_args
        body = call_args[1]["json"]
        content = body["content"]["body"]["elements"][0]["content"]
        assert "🔧 Read" in content
        assert "Some text" in content


class TestFinalize:
    """Test finalize() behavior."""

    @pytest.mark.asyncio
    async def test_finalize_cancels_flush_task(self, manager, mock_httpx_client):
        """finalize() cancels flush loop task."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_resp
        mock_httpx_client.patch.return_value = mock_resp

        await manager.start()
        assert manager._flush_task is not None

        await manager.finalize("Done")

        assert manager._flush_task.cancelled() or manager._flush_task.done()
        assert manager._finalized is True

    @pytest.mark.asyncio
    async def test_finalize_closes_httpx_client(self, manager, mock_httpx_client):
        """finalize() closes httpx client."""
        manager._sequence_created = True
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx_client.patch.return_value = mock_resp

        await manager.finalize("Done")

        mock_httpx_client.aclose.assert_called_once()


class TestRetryBehavior:
    """Test tenacity retry on 429 errors."""

    @pytest.mark.asyncio
    async def test_create_sequence_retries_on_429(self, manager, mock_httpx_client):
        """_create_sequence() retries on HTTPStatusError."""
        # First call raises 429, second succeeds
        error_resp = MagicMock()
        error_resp.status_code = 429
        error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Rate limit", request=MagicMock(), response=error_resp
        )

        success_resp = MagicMock()
        success_resp.raise_for_status = MagicMock()

        mock_httpx_client.post.side_effect = [error_resp, success_resp]

        await manager._create_sequence()

        # Should have called post twice (retry)
        assert mock_httpx_client.post.call_count == 2
        assert manager._sequence_created is True
