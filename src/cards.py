"""Card reply functions for Feishu bot — thinking card, unsupported type, update, error."""
import json

import lark_oapi as lark
from lark_oapi.core.token import TokenManager
import structlog

logger = structlog.get_logger()


def _build_card(header_template: str, body_text: str) -> str:
    """
    Build a CardKit v2 interactive card JSON string.

    Args:
        header_template: Feishu header color template (e.g. "blue", "red", "orange").
        body_text: Markdown text content for the card body.

    Returns:
        JSON string in CardKit v2 format (schema="2.0").
    """
    card = {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": "AI 助手"},
            "template": header_template,
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": body_text}
            ]
        },
    }
    return json.dumps({"data": card}, ensure_ascii=False)


# Interactive card template (per D-03: status card with header)
# For msg_type="interactive", content wraps card in {"data": {...}} with schema="2.0"
THINKING_CARD_TEMPLATE: dict = {
    "schema": "2.0",
    "header": {
        "title": {"tag": "plain_text", "content": "AI 助手"},
        "template": "blue",
    },
    "body": {
        "elements": [
            {"tag": "markdown", "content": "**正在思考中...**\n\n_请稍候_"}
        ]
    },
}


async def send_thinking_card(client: lark.Client, message_id: str) -> str:
    """
    Send a "thinking" status card as a reply to the given message.

    Builds a CardKit v2 interactive card with blue header and "正在思考中"
    body, then sends it via the IM reply API (CARD-01).

    Args:
        client: Authenticated lark.Client instance.
        message_id: The message_id to reply to.

    Returns:
        The reply message_id (needed for Phase 3 CardKit PATCH streaming).

    Raises:
        RuntimeError: If areply response is not successful.
    """
    card_content = json.dumps({"data": THINKING_CARD_TEMPLATE}, ensure_ascii=False)

    request = (
        lark.im.v1.ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            lark.im.v1.ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(card_content)
            .build()
        )
        .build()
    )

    resp = await client.im.v1.message.areply(request)
    if not resp.success():
        raise RuntimeError(f"Card reply failed: {resp.code} {resp.msg}")

    logger.debug("thinking_card_sent", message_id=message_id, reply_id=resp.data.message_id)
    return resp.data.message_id


async def send_unsupported_type_card(
    client: lark.Client, message_id: str, msg_type: str
) -> None:
    """
    Send a friendly "unsupported message type" card as a reply (per D-05).

    Args:
        client: Authenticated lark.Client instance.
        message_id: The message_id to reply to.
        msg_type: The unsupported message type string (e.g. "image", "file").
    """
    card_content = _build_card(
        header_template="orange",
        body_text=f"暂不支持该类型消息（{msg_type}），请发送文字消息",
    )

    request = (
        lark.im.v1.ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            lark.im.v1.ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(card_content)
            .build()
        )
        .build()
    )

    resp = await client.im.v1.message.areply(request)
    if not resp.success():
        logger.warning(
            "unsupported_type_card_failed",
            message_id=message_id,
            msg_type=msg_type,
            code=resp.code,
            msg=resp.msg,
        )


async def update_card_content(
    client: lark.Client, message_id: str, text: str
) -> None:
    """
    Patch an existing card message with new markdown text (blue header).

    Used to update the "thinking" card with Claude's actual response.

    Args:
        client: Authenticated lark.Client instance.
        message_id: The reply_message_id of the card to patch (returned by send_thinking_card).
        text: Markdown text to display in the card body.

    Raises:
        RuntimeError: If the patch response is not successful.
    """
    card_content = _build_card(header_template="blue", body_text=text)

    request = (
        lark.im.v1.PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            lark.im.v1.PatchMessageRequestBody.builder()
            .content(card_content)
            .build()
        )
        .build()
    )

    resp = await client.im.v1.message.apatch(request)
    if not resp.success():
        raise RuntimeError(f"Card patch failed: {resp.code} {resp.msg}")

    logger.debug("card_content_updated", message_id=message_id)


async def send_error_card(
    client: lark.Client, message_id: str, error_text: str
) -> None:
    """
    Patch an existing card message with an error message (red header, best-effort).

    Used when Claude processing fails. Does NOT raise on failure — logs warning only.

    Args:
        client: Authenticated lark.Client instance.
        message_id: The reply_message_id of the card to patch.
        error_text: Error description to display.
    """
    card_content = _build_card(
        header_template="red",
        body_text=f"出错了: {error_text}",
    )

    request = (
        lark.im.v1.PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            lark.im.v1.PatchMessageRequestBody.builder()
            .content(card_content)
            .build()
        )
        .build()
    )

    resp = await client.im.v1.message.apatch(request)
    if not resp.success():
        logger.warning(
            "error_card_patch_failed",
            message_id=message_id,
            code=resp.code,
            msg=resp.msg,
        )


async def create_streaming_card(client: lark.Client) -> str:
    """
    Create a CardKit streaming card via lark-oapi acreate.

    Returns the card_id needed for CardStreamingManager sequence API calls.

    Args:
        client: Authenticated lark.Client instance.

    Returns:
        card_id from CardKit create response.

    Raises:
        RuntimeError: If card creation fails.
    """
    card_template = {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": "AI 助手"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": "**正在思考中...**"}
            ]
        },
    }

    request = (
        lark.cardkit.v1.CreateCardRequest.builder()
        .request_body(
            lark.cardkit.v1.CreateCardRequestBody.builder()
            .data(json.dumps(card_template, ensure_ascii=False))
            .build()
        )
        .build()
    )

    resp = await client.cardkit.v1.card.acreate(request)
    if not resp.success():
        raise RuntimeError(f"CardKit create failed: {resp.code} {resp.msg}")

    logger.debug("streaming_card_created", card_id=resp.data.card_id)
    return resp.data.card_id


async def patch_im_with_card_id(
    client: lark.Client, message_id: str, card_id: str
) -> None:
    """
    Patch an IM message to embed a CardKit card_id.

    This links the IM message (from send_thinking_card) with the streaming
    CardKit card (from create_streaming_card), enabling sequence updates.

    Args:
        client: Authenticated lark.Client instance.
        message_id: The reply_message_id from send_thinking_card().
        card_id: The card_id from create_streaming_card().

    Raises:
        RuntimeError: If patch fails.
    """
    card_content = json.dumps({"card_id": card_id}, ensure_ascii=False)

    request = (
        lark.im.v1.PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            lark.im.v1.PatchMessageRequestBody.builder()
            .content(card_content)
            .build()
        )
        .build()
    )

    resp = await client.im.v1.message.apatch(request)
    if not resp.success():
        raise RuntimeError(f"IM card_id patch failed: {resp.code} {resp.msg}")

    logger.debug("im_card_id_patched", message_id=message_id, card_id=card_id)
