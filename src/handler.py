"""Event handler pipeline for Feishu bot.

Provides:
- create_handler(): factory that returns a SYNC handler for lark SDK registration
- handle_message(): async coroutine that runs the dedup/filter/card pipeline
"""
import asyncio

import structlog

from src.cards import send_thinking_card, send_unsupported_type_card
from src.filters import should_respond, parse_message_content

logger = structlog.get_logger()


def create_handler(
    loop: asyncio.AbstractEventLoop,
    api_client,
    bot_open_id: str,
    dedup_cache,
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

    Returns:
        A sync callable compatible with register_p2_im_message_receive_v1().
    """

    def on_message_receive(data) -> None:
        """
        Sync handler — called by lark SDK synchronously on the WS loop.

        CRITICAL: Must NOT be async. Must return immediately. All real work
        is delegated to handle_message() via loop.create_task().
        """
        loop.create_task(handle_message(data, api_client, bot_open_id, dedup_cache))

    return on_message_receive


async def handle_message(data, api_client, bot_open_id: str, dedup_cache) -> None:
    """
    Async event processing pipeline.

    Steps:
    1. Dedup check — skip if event_id already seen (CONN-02)
    2. Filter check — skip group messages without @mention (CONN-03)
    3. Parse message content — handle unsupported types (D-05)
    4. Send thinking card (CARD-01)

    Unexpected exceptions are caught and logged to prevent coroutine crash.

    Args:
        data: P2ImMessageReceiveV1-like event payload.
        api_client: Authenticated lark.Client instance.
        bot_open_id: Bot's own open_id for @mention detection.
        dedup_cache: DeduplicationCache instance.
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

        # Step 5: Send thinking card (CARD-01)
        reply_id = await send_thinking_card(api_client, message.message_id)
        logger.info(
            "thinking_card_sent",
            event_id=event_id,
            message_id=message.message_id,
            reply_id=reply_id,
            msg_type=msg_type,
        )

    except Exception as exc:
        # Catch-all: log unexpected errors without crashing the handler
        logger.error(
            "handle_message_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
