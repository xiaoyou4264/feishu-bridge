"""Card reply functions for Feishu bot — thinking card, unsupported type, update, error."""
import json

import lark_oapi as lark
from lark_oapi.core.token import TokenManager
import structlog

logger = structlog.get_logger()


def build_help_card() -> str:
    """
    Build a green /help card listing available commands.

    Returns:
        JSON string for msg_type="interactive" with green header and command list.
    """
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": "小爱使用指南"},
            "template": "green",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    "**可用命令**\n\n"
                    "- `/new` — 重置会话，开始新对话\n"
                    "- `/status` — 查看运行状态\n"
                    "- `/model` — 查看当前模型配置\n"
                    "- `/restart` — 重启所有 Claude 连接\n"
                    "- `/help` — 显示此帮助信息\n"
                ),
            }
        ],
    }
    return json.dumps(card, ensure_ascii=False)


# Stage-specific card titles
TITLE_THINKING = "小爱深思熟虑中~"
TITLE_STREAMING = "小爱正在殴打你的任务"
TITLE_DONE = "小爱大功告成！"
TITLE_ERROR = "小爱出了点岔子"


def _build_card(header_template: str, body_text: str, title: str = TITLE_DONE) -> str:
    """
    Build a CardKit v2 interactive card JSON string.

    Args:
        header_template: Feishu header color template (e.g. "blue", "red", "orange").
        body_text: Markdown text content for the card body.
        title: Card header title text.

    Returns:
        JSON string in CardKit v2 format (schema="2.0") wrapped in {"data": {...}}.
    """
    card = {
        "data": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": header_template,
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": body_text}
                ]
            },
        }
    }
    return json.dumps(card, ensure_ascii=False)


# Interactive card template for msg_type="interactive" — CardKit v2 format
THINKING_CARD_TEMPLATE: dict = {
    "data": {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": TITLE_THINKING},
            "template": "blue",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": "**正在思考中...**\n\n_请稍候_"}
            ]
        },
    }
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


async def send_streaming_reply(client: lark.Client, message_id: str) -> tuple[str, str]:
    """
    Create a CardKit streaming card and send it as a reply.

    Returns (reply_message_id, card_id) — both needed for streaming updates.
    """
    card_id = await create_streaming_card(client)

    content = json.dumps({"type": "card", "data": {"card_id": card_id}})
    request = (
        lark.im.v1.ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            lark.im.v1.ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(content)
            .build()
        )
        .build()
    )

    resp = await client.im.v1.message.areply(request)
    if not resp.success():
        raise RuntimeError(f"Streaming reply failed: {resp.code} {resp.msg}")

    logger.debug("streaming_reply_sent", message_id=message_id, reply_id=resp.data.message_id, card_id=card_id)
    return resp.data.message_id, card_id


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


def _build_card_with_buttons(header_template: str, body_text: str, buttons: dict, title: str = TITLE_DONE) -> str:
    """Build an interactive card with markdown body and action buttons (CardKit v2 format)."""
    card = {
        "data": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": header_template,
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": body_text},
                    buttons,
                ]
            },
        }
    }
    return json.dumps(card, ensure_ascii=False)


def build_stop_button(reply_message_id: str) -> dict:
    """Build a CardKit v2 action element with a Stop button using callback behaviors."""
    return {
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "停止"},
                "type": "danger",
                "behaviors": [
                    {
                        "type": "callback",
                        "value": {"action": "stop", "message_id": reply_message_id},
                    }
                ],
            }
        ],
    }


def build_feedback_buttons(reply_message_id: str) -> dict:
    """Build a CardKit v2 action element with thumbs up/down feedback buttons."""
    return {
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "👍"},
                "type": "default",
                "action_type": "request",
                "value": {"action": "thumbs_up", "message_id": reply_message_id},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "👎"},
                "type": "default",
                "action_type": "request",
                "value": {"action": "thumbs_down", "message_id": reply_message_id},
            },
        ],
    }



async def update_card_content(
    client: lark.Client, message_id: str, text: str, buttons: dict | None = None, title: str = TITLE_DONE
) -> None:
    """
    Patch an existing card message with new markdown text.

    Used to update the "thinking" card with Claude's actual response.

    Args:
        client: Authenticated lark.Client instance.
        message_id: The reply_message_id of the card to patch (returned by send_thinking_card).
        text: Markdown text to display in the card body.
        buttons: Optional CardKit v2 action element to append to the card body.
        title: Card header title text.

    Raises:
        RuntimeError: If the patch response is not successful.
    """
    if buttons is not None:
        card_content = _build_card_with_buttons(header_template="blue", body_text=text, buttons=buttons, title=title)
    else:
        card_content = _build_card(header_template="blue", body_text=text, title=title)

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
        title=TITLE_ERROR,
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


async def create_streaming_card(client: lark.Client, stop_message_id: str | None = None) -> str:
    """
    Create a CardKit streaming card via lark-oapi acreate.

    Returns the card_id needed for CardStreamingManager sequence API calls.

    Args:
        client: Authenticated lark.Client instance.
        stop_message_id: If provided, adds a Stop button to the initial card body (INTER-01).

    Returns:
        card_id from CardKit create response.

    Raises:
        RuntimeError: If card creation fails.
    """
    from src.card_streaming import STREAMING_ELEMENT_ID

    # NOTE: CardKit v2 schema does NOT support "action" tag (buttons).
    # Stop button cannot be in the streaming card. It would need a separate mechanism.
    elements: list[dict] = [
        {"tag": "markdown", "content": "**正在思考中...**", "element_id": STREAMING_ELEMENT_ID}
    ]

    card_template = {
        "schema": "2.0",
        "config": {
            "streaming_mode": True,
            "streaming_config": {
                "print_frequency_ms": {"default": 50},
                "print_step": {"default": 2},
                "print_strategy": "fast",
            },
        },
        "header": {
            "title": {"tag": "plain_text", "content": TITLE_STREAMING},
            "template": "blue",
        },
        "body": {
            "elements": elements
        },
    }

    request = (
        lark.cardkit.v1.CreateCardRequest.builder()
        .request_body(
            lark.cardkit.v1.CreateCardRequestBody.builder()
            .type("card_json")
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
    # Correct format for sending CardKit card via IM: {"type": "card", "data": {"card_id": xxx}}
    card_content = json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False)

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
