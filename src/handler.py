"""Event handler pipeline for Feishu bot.

Provides:
- create_handler(): factory that returns a SYNC handler for lark SDK registration
- handle_message(): async coroutine that runs the dedup/filter/card pipeline and
  dispatches to Claude via SessionManager
"""
import asyncio
import json
import time

import lark_oapi as lark
import structlog

# Module-level start time for /status uptime reporting
_start_time = time.monotonic()
from structlog.contextvars import bind_contextvars, clear_contextvars

from src.cards import send_thinking_card, send_streaming_reply, send_unsupported_type_card
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
    Factory: returns a SYNC handler for card action callbacks (INTER-01, INTER-02).

    Per D-28: registers card.action.trigger callback via long connection.
    Per Pitfall 6: handler MUST be sync (not async). The lark SDK calls it synchronously.
    Per INTER-01: Stop button cancels the running Claude task.
    Per INTER-02: Feedback buttons log structlog event.

    Returns:
        A sync callable compatible with register_p2_card_action_trigger().
    """

    def on_card_action(data) -> "P2CardActionTriggerResponse":
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse, CallBackToast,
        )
        from src.claude_worker import cancel_task_for_message

        resp = P2CardActionTriggerResponse()

        # Extract action value from callback data
        action_value = {}
        try:
            action_value = data.event.action.value or {}
        except Exception:
            pass

        action_type = action_value.get("action")
        message_id = action_value.get("message_id")

        if action_type == "stop" and message_id:
            cancelled = cancel_task_for_message(message_id)
            toast = CallBackToast()
            toast.type = "info"
            toast.content = "已停止" if cancelled else "任务已完成"
            resp.toast = toast
            logger.info("stop_button_clicked", message_id=message_id, cancelled=cancelled)

        elif action_type in ("thumbs_up", "thumbs_down") and message_id:
            operator_id = None
            try:
                operator_id = data.event.operator.open_id
            except Exception:
                pass
            logger.info(
                "feedback_received",
                feedback=action_type,
                message_id=message_id,
                operator_id=operator_id,
            )
            toast = CallBackToast()
            toast.type = "success"
            toast.content = "感谢反馈！"
            resp.toast = toast

        else:
            # Unknown action — log for debugging
            logger.debug("unknown_card_action", action_value=action_value)

        return resp

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

        # Bind event_id to structlog context so all log lines in this pipeline include it (D-35)
        # MUST be called BEFORE asyncio.create_task() — child task inherits context snapshot (Pitfall 3)
        bind_contextvars(event_id=event_id)

        try:
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

            # Step 5: /help command — send green help card (SESS-04)
            if text.strip().lower() == "/help":
                from src.cards import build_help_card
                request = (
                    lark.im.v1.ReplyMessageRequest.builder()
                    .message_id(message.message_id)
                    .request_body(
                        lark.im.v1.ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(build_help_card())
                        .build()
                    )
                    .build()
                )
                await api_client.im.v1.message.areply(request)
                logger.info("help_command", event_id=event_id)
                return

            # Step 6: /new command — reset session and send confirmation card (SESS-03, D-17)
            if text.strip().lower() == "/new":
                session_key = get_session_key(
                    message.chat_type,
                    data.event.sender.sender_id.open_id,
                    getattr(message, "chat_id", ""),
                )
                await session_manager.destroy(session_key)

                # Send green confirmation card via areply
                card = {
                    "header": {
                        "title": {"tag": "plain_text", "content": "小爱收到~"},
                        "template": "green",
                    },
                    "elements": [
                        {"tag": "markdown", "content": "会话已重置，开始新对话吧！"}
                    ],
                }
                request = (
                    lark.im.v1.ReplyMessageRequest.builder()
                    .message_id(message.message_id)
                    .request_body(
                        lark.im.v1.ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(json.dumps(card, ensure_ascii=False))
                        .build()
                    )
                    .build()
                )
                await api_client.im.v1.message.areply(request)
                logger.info("session_reset", event_id=event_id, session_key=session_key)
                return

            # Step 6b: /status command — show runtime status
            if text.strip().lower() == "/status":
                uptime_secs = int(time.monotonic() - _start_time)
                hours, remainder = divmod(uptime_secs, 3600)
                mins, secs = divmod(remainder, 60)
                uptime_str = f"{hours}h{mins}m{secs}s" if hours else f"{mins}m{secs}s"
                active_sessions = len(session_manager._sessions)
                from src.claude_worker import _active_tasks, _queue
                active_tasks = len(_active_tasks)
                queue_len = len(_queue)

                status_text = (
                    f"**运行状态**\n\n"
                    f"⏱ 运行时长：`{uptime_str}`\n"
                    f"💬 活跃会话：`{active_sessions}`\n"
                    f"⚡ 进行中任务：`{active_tasks}`\n"
                    f"📋 排队中：`{queue_len}`\n"
                    f"🔧 工作目录：`{config.working_dir}`\n"
                    f"⏰ 超时设置：`{config.claude_timeout}s`\n"
                    f"🔒 最大并发：`{config.max_concurrent_tasks}`"
                )
                card = {
                    "header": {
                        "title": {"tag": "plain_text", "content": "小爱运行状态"},
                        "template": "blue",
                    },
                    "elements": [{"tag": "markdown", "content": status_text}],
                }
                request = (
                    lark.im.v1.ReplyMessageRequest.builder()
                    .message_id(message.message_id)
                    .request_body(
                        lark.im.v1.ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(json.dumps(card, ensure_ascii=False))
                        .build()
                    )
                    .build()
                )
                await api_client.im.v1.message.areply(request)
                logger.info("status_command", event_id=event_id)
                return

            # Step 6c: /model command — show current model info
            if text.strip().lower() == "/model":
                model_name = getattr(session_manager._options, 'model', None) or "默认 (claude-sonnet-4-20250514)"
                permission = session_manager._options.permission_mode or "default"
                model_text = (
                    f"**当前模型配置**\n\n"
                    f"🤖 模型：`{model_name}`\n"
                    f"🔑 权限模式：`{permission}`\n"
                    f"🛠 工具限制：`{'无限制' if not config.allowed_tools else ', '.join(config.allowed_tools)}`"
                )
                card = {
                    "header": {
                        "title": {"tag": "plain_text", "content": "小爱模型信息"},
                        "template": "purple",
                    },
                    "elements": [{"tag": "markdown", "content": model_text}],
                }
                request = (
                    lark.im.v1.ReplyMessageRequest.builder()
                    .message_id(message.message_id)
                    .request_body(
                        lark.im.v1.ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(json.dumps(card, ensure_ascii=False))
                        .build()
                    )
                    .build()
                )
                await api_client.im.v1.message.areply(request)
                logger.info("model_command", event_id=event_id)
                return

            # Step 6d: /restart command — destroy all sessions, reconnect fresh
            if text.strip().lower() == "/restart":
                session_keys = list(session_manager._sessions.keys())
                for key in session_keys:
                    await session_manager.destroy(key)
                card = {
                    "header": {
                        "title": {"tag": "plain_text", "content": "小爱已重启"},
                        "template": "green",
                    },
                    "elements": [
                        {"tag": "markdown", "content": f"已断开 **{len(session_keys)}** 个会话的 Claude 连接并重新初始化。\n\n下次发消息时会自动建立新连接。"}
                    ],
                }
                request = (
                    lark.im.v1.ReplyMessageRequest.builder()
                    .message_id(message.message_id)
                    .request_body(
                        lark.im.v1.ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(json.dumps(card, ensure_ascii=False))
                        .build()
                    )
                    .build()
                )
                await api_client.im.v1.message.areply(request)
                logger.info("restart_command", event_id=event_id, sessions_destroyed=len(session_keys))
                return

            # Step 7: Send streaming CardKit card as reply (CARD-01 + CARD-02)
            card_id = None
            think_start = time.monotonic()
            try:
                reply_id, card_id = await send_streaming_reply(api_client, message.message_id)
            except Exception as stream_err:
                logger.warning("streaming_reply_fallback", error=str(stream_err))
                reply_id = await send_thinking_card(api_client, message.message_id)
            logger.info(
                "thinking_card_sent",
                event_id=event_id,
                message_id=message.message_id,
                reply_id=reply_id,
                card_id=card_id,
                msg_type=msg_type,
            )

            # Step 8: Resolve session and format prompt
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

            # Step 9: Create isolated Task per message (CONC-01, CLAUDE-04)
            # bind_contextvars() was called above — child task inherits event_id context snapshot
            asyncio.create_task(
                single_turn_worker(
                    session=session,
                    prompt=prompt,
                    reply_message_id=reply_id,
                    card_id=card_id,
                    api_client=api_client,
                    semaphore=session_manager.semaphore,
                    timeout=config.claude_timeout,
                    think_start=think_start,
                )
            )
            logger.info("claude_task_dispatched", event_id=event_id, session_key=session_key)

        finally:
            # Always clear contextvars after processing — prevents leaking event_id to next event
            clear_contextvars()

    except Exception as exc:
        # Catch-all: log unexpected errors without crashing the handler
        logger.error(
            "handle_message_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
