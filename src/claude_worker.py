"""Claude single-turn worker — runs one Claude turn with semaphore + lock + timeout.

Provides:
- _run_claude_turn(): calls query(), drains receive_response(), returns text (kept for backward compat)
- _run_claude_turn_streaming(): streaming version with CardStreamingManager callbacks
- single_turn_worker(): semaphore OUTER, lock INNER, timeout via wait_for, streaming card on success,
  error card on failure

The worker MUST NOT propagate exceptions — each message is isolated (CLAUDE-04).
"""
import asyncio
import uuid

import structlog
from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, StreamEvent, TextBlock, ToolUseBlock, ToolResultBlock

from src.card_streaming import CardStreamingManager
from src.cards import send_error_card, update_card_content, create_streaming_card, patch_im_with_card_id, build_feedback_buttons
from src.session import SessionState

logger = structlog.get_logger()

# Module-level registry: reply_message_id -> asyncio.Task (D-41)
_active_tasks: dict[str, asyncio.Task] = {}


def cancel_task_for_message(message_id: str) -> bool:
    """Cancel the task for a given message_id. Returns True if found and cancelled."""
    task = _active_tasks.pop(message_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False


async def _run_claude_turn(client: ClaudeSDKClient, prompt: str) -> str:
    """
    Run one Claude turn: query then drain response.

    Calls client.query(prompt), then iterates receive_response() collecting
    all TextBlock content from AssistantMessages. ResultMessage auto-terminates
    the iterator.

    Args:
        client: Connected ClaudeSDKClient instance.
        prompt: Text to send to Claude.

    Returns:
        Concatenated text from all AssistantMessage TextBlocks.
    """
    await client.query(prompt)
    text_parts: list[str] = []
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        # ResultMessage ends the iterator automatically — no explicit break needed
    return "".join(text_parts)


async def _run_claude_turn_streaming(
    client: ClaudeSDKClient,
    prompt: str,
    manager: CardStreamingManager,
) -> str:
    """
    Run one Claude turn with streaming callbacks to CardStreamingManager.

    Per D-24/D-25: tool calls shown as they happen.
    Per CARD-02: text streamed via manager.append_text().
    ResultMessage terminates iteration and signals finalize().

    Args:
        client: Connected ClaudeSDKClient instance.
        prompt: Text to send to Claude.
        manager: CardStreamingManager instance to receive streaming callbacks.

    Returns:
        Concatenated text from all AssistantMessage TextBlocks.
    """
    await client.query(prompt)
    full_text_parts: list[str] = []
    async for msg in client.receive_response():
        if isinstance(msg, StreamEvent):
            # Raw Anthropic API stream event — extract text deltas
            event = msg.event
            event_type = event.get("type", "")
            if event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        full_text_parts.append(text)
                        await manager.append_text(text)
        elif isinstance(msg, AssistantMessage):
            # Final complete message — extract any tool use/result blocks
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    await manager.append_tool_use(block.name, block.input)
                elif isinstance(block, ToolResultBlock):
                    content = block.content if isinstance(block.content, str) else str(block.content or "")
                    await manager.append_tool_result(content, bool(block.is_error))
    return "".join(full_text_parts)


async def _show_error(card_id, manager, api_client, reply_message_id, error_text):
    """Show error message — via CardKit PUT for v2 cards, via IM patch for v1 cards."""
    try:
        if card_id and manager:
            # v2 card: finalize with error text (closes streaming mode)
            if not manager._finalized:
                await manager.finalize(f"❌ {error_text}")
        else:
            # v1 card: use IM patch
            await send_error_card(api_client, reply_message_id, error_text)
    except Exception as e:
        logger.error("show_error_failed", error=str(e))


async def single_turn_worker(
    session: SessionState,
    prompt: str,
    reply_message_id: str,
    api_client,
    semaphore: asyncio.Semaphore,
    timeout: float,
    card_id: str | None = None,
) -> None:
    """
    Process one Claude turn with concurrency control, timeout, and streaming card.

    Locking order (Pitfall 4 deadlock prevention):
      1. semaphore — OUTER: global cap on concurrent Claude subprocesses (D-11)
      2. session.lock — INNER: per-session serialization (Pitfall 1)

    On success: creates CardKit streaming card, patches IM message, streams response
                via CardStreamingManager, finalizes sequence.
    On asyncio.TimeoutError: calls finalize() + send_error_card() with timeout message (CLAUDE-05).
    On any other Exception: finalizes sequence, calls send_error_card() (CLAUDE-04).
    Exceptions do NOT propagate — each worker is fully isolated.

    Args:
        session: SessionState with client and per-session lock.
        prompt: Formatted prompt string to send to Claude.
        reply_message_id: Message ID of the "thinking" card to update.
        api_client: Authenticated lark.Client instance for card updates.
        semaphore: Global asyncio.Semaphore capping concurrent tasks.
        timeout: Maximum seconds to wait for Claude response (CLAUDE_TIMEOUT).
    """
    # Register current task in active registry (D-41)
    current_task = asyncio.current_task()
    _active_tasks[reply_message_id] = current_task
    try:
        async with semaphore:  # OUTER: global concurrency cap
            async with session.lock:  # INNER: per-session serialization
                manager = None
                try:
                    if card_id:
                        # Streaming path: card already created and sent by handler
                        from lark_oapi.core.token import TokenManager
                        tenant_token = TokenManager.get_self_tenant_token(api_client._config)
                        manager = CardStreamingManager(card_id=card_id, tenant_token=tenant_token)
                        await manager.start()
                        try:
                            result_text = await asyncio.wait_for(
                                _run_claude_turn_streaming(session.client, prompt, manager),
                                timeout=timeout,
                            )
                        finally:
                            # Always finalize — ensures streaming_mode is closed
                            if not manager._finalized:
                                await manager.finalize(result_text if 'result_text' in dir() else "")
                        # Streaming done — no IM patch needed (v2 card updated via PUT element content)
                    else:
                        # Simple path: no card_id, use basic card update
                        result_text = await asyncio.wait_for(
                            _run_claude_turn(session.client, prompt),
                            timeout=timeout,
                        )
                        await update_card_content(api_client, reply_message_id, result_text)
                except asyncio.TimeoutError:
                    logger.warning("claude_worker_timeout", session_key=session.session_key, timeout=timeout)
                    await _show_error(card_id, manager, api_client, reply_message_id, f"响应超时（>{timeout:.0f}s），请重试")
                except asyncio.CancelledError:
                    logger.info("claude_worker_cancelled", session_key=session.session_key)
                    await _show_error(card_id, manager, api_client, reply_message_id, "**已停止** - 用户取消了请求")
                    raise
                except Exception as exc:
                    logger.error("claude_worker_error", session_key=session.session_key, error=str(exc), error_type=type(exc).__name__)
                    await _show_error(card_id, manager, api_client, reply_message_id, f"处理出错：{exc}")
    finally:
        _active_tasks.pop(reply_message_id, None)
