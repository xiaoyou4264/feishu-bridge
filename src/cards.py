"""Card reply functions for Feishu bot — thinking card and unsupported type card."""
import json

import lark_oapi as lark
import structlog

logger = structlog.get_logger()

# CardKit v2 thinking card template (per D-03: status card with header)
THINKING_CARD_TEMPLATE: dict = {
    "type": "card",
    "data": {
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
    card_content = json.dumps(THINKING_CARD_TEMPLATE, ensure_ascii=False)

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
    card = {
        "type": "card",
        "data": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "AI 助手"},
                "template": "orange",
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            f"暂不支持该类型消息（{msg_type}），请发送文字消息"
                        ),
                    }
                ]
            },
        },
    }

    card_content = json.dumps(card, ensure_ascii=False)

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
