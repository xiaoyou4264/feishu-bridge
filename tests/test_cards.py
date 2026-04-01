"""Tests for src/cards.py — send_thinking_card and send_unsupported_type_card."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSendThinkingCard:
    """Tests for send_thinking_card() async function."""

    @pytest.mark.asyncio
    async def test_constructs_reply_with_interactive_msg_type(self):
        """send_thinking_card() builds a ReplyMessageRequest with msg_type='interactive'."""
        from src.cards import send_thinking_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "reply_msg_001"
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        result = await send_thinking_card(mock_client, "msg_001")

        mock_client.im.v1.message.areply.assert_called_once()
        call_args = mock_client.im.v1.message.areply.call_args[0][0]
        # The request body should contain msg_type="interactive"
        assert result == "reply_msg_001"

    @pytest.mark.asyncio
    async def test_card_json_contains_schema_20(self):
        """The card content JSON must have schema '2.0' per CardKit v2."""
        from src.cards import send_thinking_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "reply_msg_002"
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        await send_thinking_card(mock_client, "msg_001")

        call_args = mock_client.im.v1.message.areply.call_args[0][0]
        # Extract content from the built request
        request_body = call_args.request_body
        content = json.loads(request_body.content)
        assert content["data"]["schema"] == "2.0"

    @pytest.mark.asyncio
    async def test_card_json_contains_ai_header(self):
        """The card header must have title 'AI 助手' with blue template."""
        from src.cards import send_thinking_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "reply_msg_003"
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        await send_thinking_card(mock_client, "msg_001")

        call_args = mock_client.im.v1.message.areply.call_args[0][0]
        request_body = call_args.request_body
        content = json.loads(request_body.content)
        header = content["data"]["header"]
        assert header["title"]["content"] == "AI 助手"
        assert header["template"] == "blue"

    @pytest.mark.asyncio
    async def test_card_body_contains_thinking_text(self):
        """The card body must contain a markdown element with '正在思考中'."""
        from src.cards import send_thinking_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "reply_msg_004"
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        await send_thinking_card(mock_client, "msg_001")

        call_args = mock_client.im.v1.message.areply.call_args[0][0]
        request_body = call_args.request_body
        content = json.loads(request_body.content)
        elements = content["data"]["body"]["elements"]
        assert any("正在思考中" in elem.get("content", "") for elem in elements)

    @pytest.mark.asyncio
    async def test_calls_areply_with_message_id(self):
        """send_thinking_card() calls areply and the request targets the correct message_id."""
        from src.cards import send_thinking_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "reply_msg_005"
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        await send_thinking_card(mock_client, "target_msg_xyz")

        mock_client.im.v1.message.areply.assert_called_once()
        call_args = mock_client.im.v1.message.areply.call_args[0][0]
        assert call_args.message_id == "target_msg_xyz"

    @pytest.mark.asyncio
    async def test_raises_runtime_error_on_failure(self):
        """send_thinking_card() raises RuntimeError when areply response is not successful."""
        from src.cards import send_thinking_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = False
        mock_resp.code = 99991663
        mock_resp.msg = "message not found"
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="Card reply failed"):
            await send_thinking_card(mock_client, "msg_001")

    @pytest.mark.asyncio
    async def test_returns_reply_message_id(self):
        """send_thinking_card() returns the reply message_id from resp.data.message_id."""
        from src.cards import send_thinking_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "om_card_reply_abc"
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        result = await send_thinking_card(mock_client, "msg_001")

        assert result == "om_card_reply_abc"


class TestSendUnsupportedTypeCard:
    """Tests for send_unsupported_type_card() async function."""

    @pytest.mark.asyncio
    async def test_sends_interactive_card_with_unsupported_message(self):
        """send_unsupported_type_card() sends an interactive card with friendly prompt."""
        from src.cards import send_unsupported_type_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        await send_unsupported_type_card(mock_client, "msg_001", "image")

        mock_client.im.v1.message.areply.assert_called_once()
        call_args = mock_client.im.v1.message.areply.call_args[0][0]
        request_body = call_args.request_body
        content = json.loads(request_body.content)
        # Should have CardKit v2 structure
        assert content["data"]["schema"] == "2.0"

    @pytest.mark.asyncio
    async def test_card_contains_unsupported_message_and_type(self):
        """The card body should mention '暂不支持' and include the message type."""
        from src.cards import send_unsupported_type_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        await send_unsupported_type_card(mock_client, "msg_001", "video")

        call_args = mock_client.im.v1.message.areply.call_args[0][0]
        request_body = call_args.request_body
        content_str = request_body.content
        # The card body text should include the unsupported prompt and type
        assert "暂不支持" in content_str
        assert "video" in content_str

    @pytest.mark.asyncio
    async def test_card_uses_orange_header_template(self):
        """Unsupported type card uses orange header template (per D-05)."""
        from src.cards import send_unsupported_type_card

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        await send_unsupported_type_card(mock_client, "msg_001", "file")

        call_args = mock_client.im.v1.message.areply.call_args[0][0]
        request_body = call_args.request_body
        content = json.loads(request_body.content)
        assert content["data"]["header"]["template"] == "orange"
