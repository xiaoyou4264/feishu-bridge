"""Event handler pipeline for Feishu bot.

Provides:
- create_handler(): factory that returns a SYNC handler for lark SDK registration
- handle_message(): async coroutine that runs the dedup/filter/card pipeline and
  dispatches to Claude via SessionManager
"""
import asyncio
import json

import lark_oapi as lark
import structlog

from src.cards import send_thinking_card, send_unsupported_type_card
from src.claude_worker import single_turn_worker
from src.filters import should_respond, parse_message_content
from src.session import SessionManager, get_session_key, get_display_name, format_prompt

logger = structlog.get_logger()


def create_handler(
    loop: asyncio.AbstractEventLoop,
    api_client,
    bot_open_id: str,
    dedup_cache,
    session_manager: SessionManager,
    config,
):
    """
    Factory: returns a SYNC event handler for lark SDK registration.

    The lark SDK calls registered handlers synchronously (Pitfall 1).
    This factory returns a sync function that schedules async work via
    loop.create_task() — it never blocks the WS event loop.

    Args:
        loop: The asyncio event loop captured AFTER importing lark_oapi.ws.
        api_client: Authenticated lark.Client instance.
        bot_open_id: The bot's own open_id for @mention detection (CONN-03).
        dedup_cache: DeduplicationCache instance for event deduplication (CONN-02).
        session_manager: SessionManager instance for Claude session lifecycle.
        config: Config instance with claude_timeout and other settings.

    Returns:
        A sync callable compatible with register_p2_im_message_receive_v1().
    """

    def on_message_receive(data) -> None:
        """
        Sync handler — called by lark SDK synchronously on the WS loop.

        CRITICAL: Must NOT be async. Must return immediately. All real work
        is delegated to handle_message() via loop.create_task().
        """
        loop.create_task(
            handle_message(data, api_client, bot_open_id, dedup_cache, session_manager, config)
        )

    return on_message_receive


def create_card_action_handler():
    """
    Factory: returns a SYNC handler for card action callbacks (INTER-03).

    Per D-28: registers card.action.trigger callback via long connection.
    Per D-29: Phase 3 only builds infrastructure — no button logic yet.
    Per Pitfall 6: handler MUST be sync (not async). The lark SDK calls it synchronously.

    Returns:
        A sync callable compatible with register_p2_card_action_trigger().
    """

    def on_card_action(data) -> "P2CardActionTriggerResponse":
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

        # Phase 3: log and return empty response (D-29)
        action_tag = None
        operator_id = None
        try:
            action_tag = getattr(data.action, "tag", None) if hasattr(data, "action") else None
            operator_id = getattr(data.operator, "open_id", None) if hasattr(data, "operator") else None
        except Exception:
            pass
        logger.info("card_action_received", action_tag=action_tag, operator_id=operator_id)
        return P2CardActionTriggerResponse()

    return on_card_action


async def handle_message(
    data,
    api_client,
    bot_open_id: str,
    dedup_cache,
    session_manager: SessionManager,
    config,
) -> None:
    """
    Async event processing pipeline.

    Steps:
    1. Dedup check — skip if event_id already seen (CONN-02)
    2. Filter check — skip group messages without @mention (CONN-03)
    3. Parse message content — handle unsupported types (D-05)
    4. /new command check — reset session and send confirmation card (SESS-03, D-17)
    5. Send thinking card (CARD-01)
    6. Dispatch to Claude via asyncio.Task (CONC-01, CLAUDE-04)

    Unexpected exceptions are caught and logged to prevent coroutine crash.

    Args:
        data: P2ImMessageReceiveV1-like event payload.
        api_client: Authenticated lark.Client instance.
        bot_open_id: Bot's own open_id for @mention detection.
        dedup_cache: DeduplicationCache instance.
        session_manager: SessionManager for Claude session lifecycle.
        config: Config instance (claude_timeout, etc.).
    """
    try:
        # Step 1: Dedup — use event_id (per CONN-02 and research recommendation)
        event_id = data.header.event_id
        if dedup_cache.is_duplicate(event_id):
            logger.debug("event_deduplicated", event_id=event_id)
            return

        # Step 2: Extract message
        message = data.event.message

        # Step 3: Filter — group messages require @mention (CONN-03)
        if not should_respond(message, bot_open_id):
            logger.debug(
                "message_filtered",
                event_id=event_id,
                chat_type=message.chat_type,
            )
            return

        # Step 4: Parse message type (D-04, D-05)
        try:
            text, msg_type = parse_message_content(message)
        except ValueError as exc:
            # Unsupported message type — send friendly prompt (D-05)
            err_msg = str(exc)
            if err_msg.startswith("unsupported_type:"):
                unsupported_type = err_msg.split(":", 1)[1]
                logger.info(
                    "unsupported_message_type",
                    event_id=event_id,
                    msg_type=unsupported_type,
                )
                await send_unsupported_type_card(
                    api_client, message.message_id, unsupported_type
                )
                return
            # Re-raise unexpected ValueError
            raise

        # Step 5: /new command — reset session and send confirmation card (SESS-03, D-17)
        if text.strip().lower() == "/new":
            session_key = get_session_key(
                message.chat_type,
                data.event.sender.sender_id.open_id,
                getattr(message, "chat_id", ""),
            )
            await session_manager.destroy(session_key)

            # Send green confirmation card via areply
            card = {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": "AI 助手"},
                    "template": "green",
                },
                "body": {
                    "elements": [
                        {"tag": "markdown", "content": "会话已重置，开始新对话吧！"}
                    ]
                },
            }
            request = (
                lark.im.v1.ReplyMessageRequest.builder()
                .message_id(message.message_id)
                .request_body(
                    lark.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(json.dumps({"data": card}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            await api_client.im.v1.message.areply(request)
            logger.info("session_reset", event_id=event_id, session_key=session_key)
            return

        # Step 6: Send thinking card (CARD-01)
        reply_id = await send_thinking_card(api_client, message.message_id)
        logger.info(
            "thinking_card_sent",
            event_id=event_id,
            message_id=message.message_id,
            reply_id=reply_id,
            msg_type=msg_type,
        )

        # Step 7: Resolve session and format prompt
        session_key = get_session_key(
            message.chat_type,
            data.event.sender.sender_id.open_id,
            getattr(message, "chat_id", ""),
        )
        session = await session_manager.get_or_create(session_key)

        # Group chat: fetch display_name and format prompt with prefix (D-14)
        if message.chat_type == "group":
            display_name = await get_display_name(
                api_client, data.event.sender.sender_id.open_id, session.name_cache
            )
            prompt = format_prompt(text, "group", display_name)
        else:
            prompt = format_prompt(text, "p2p")

        # Step 8: Create isolated Task per message (CONC-01, CLAUDE-04)
        asyncio.create_task(
            single_turn_worker(
                session=session,
                prompt=prompt,
                reply_message_id=reply_id,
                api_client=api_client,
                semaphore=session_manager.semaphore,
                timeout=config.claude_timeout,
            )
        )
        logger.info("claude_task_dispatched", event_id=event_id, session_key=session_key)

    except Exception as exc:
        # Catch-all: log unexpected errors without crashing the handler
        logger.error(
            "handle_message_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
