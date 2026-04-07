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

# Global queue tracking for queue position display
_queue: list[dict] = []  # [{"reply_id": str, "card_id": str|None, "prompt_preview": str, "api_client": any}]
_queue_lock = asyncio.Lock()


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


async def _update_queue_cards():
    """Update all queued cards with their current position."""
    async with _queue_lock:
        for i, entry in enumerate(_queue):
            position = i + 1
            total = len(_queue)
            try:
                if entry["card_id"]:
                    # v2 streaming card: PUT element content
                    from lark_oapi.core.token import TokenManager
                    token = TokenManager.get_self_tenant_token(entry["api_client"]._config)
                    import httpx
                    url = f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{entry['card_id']}/elements/md_stream/content"
                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
                    text = f"⏳ **排队中** ({position}/{total})\n\n前方还有 {position - 1} 个任务"
                    entry["seq"] = entry.get("seq", 0) + 1
                    async with httpx.AsyncClient(timeout=5) as http:
                        await http.put(url, headers=headers, json={"content": text, "sequence": entry["seq"]})
                else:
                    # v1 card: use IM patch
                    await update_card_content(entry["api_client"], entry["reply_id"],
                                              f"⏳ **排队中** ({position}/{total})\n\n前方还有 {position - 1} 个任务")
            except Exception as e:
                logger.debug("queue_card_update_failed", error=str(e))


async def _show_error(card_id, manager, api_client, reply_message_id, error_text):
    """Show error message — via CardKit PUT for streaming cards, via IM patch for simple cards."""
    try:
        if card_id and manager:
            # Streaming card: finalize with error text (updates content + closes streaming mode)
            # Do NOT attempt IM patch — streaming cards reject card JSON format changes
            if not manager._finalized:
                await manager.finalize(f"❌ {error_text}", is_error=True)
        else:
            # Simple card: use IM patch
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
    think_start: float | None = None,
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

    # Check if we need to queue (semaphore full)
    queue_entry = {"reply_id": reply_message_id, "card_id": card_id,
                   "prompt_preview": prompt[:30], "api_client": api_client, "seq": 0}
    need_queue = semaphore._value == 0  # All slots taken
    if need_queue:
        async with _queue_lock:
            _queue.append(queue_entry)
        await _update_queue_cards()
        logger.info("task_queued", reply_id=reply_message_id, position=len(_queue))

    # Create and start streaming manager early — timer runs during think/queue phase
    manager = None
    if card_id:
        from lark_oapi.core.token import TokenManager
        tenant_token = TokenManager.get_self_tenant_token(api_client._config)
        manager = CardStreamingManager(card_id=card_id, tenant_token=tenant_token, think_start=think_start)
        await manager.start()

    try:
        async with semaphore:  # OUTER: global concurrency cap
            # Remove from queue when we get the semaphore
            if need_queue:
                async with _queue_lock:
                    if queue_entry in _queue:
                        _queue.remove(queue_entry)
                await _update_queue_cards()
                logger.info("task_dequeued", reply_id=reply_message_id)
            async with session.lock:  # INNER: per-session serialization
                try:
                    if card_id and manager:
                        # Streaming path: manager already running
                        # NOTE: mark_stream_start() is called lazily on first text token
                        # (via append_text), NOT here — so think time reflects actual
                        # Claude processing time, not just lock-acquisition time.
                        try:
                            result_text = await asyncio.wait_for(
                                _run_claude_turn_streaming(session.client, prompt, manager),
                                timeout=timeout,
                            )
                        finally:
                            # Always finalize — ensures streaming_mode is closed
                            if not manager._finalized:
                                await manager.finalize(result_text if 'result_text' in dir() else "")
                        # Streaming done — content updated via CardKit PUT element API
                        # Header can't be changed on streaming cards (settings API and IM patch both fail)
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
