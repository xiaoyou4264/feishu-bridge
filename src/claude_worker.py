"""Claude single-turn worker — runs one Claude turn with semaphore + lock + timeout.

Provides:
- _run_claude_turn(): calls query(), drains receive_response(), returns text
- single_turn_worker(): semaphore OUTER, lock INNER, timeout via wait_for, error card on failure

The worker MUST NOT propagate exceptions — each message is isolated (CLAUDE-04).
"""
import asyncio

import structlog
from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, TextBlock

from src.cards import send_error_card, update_card_content
from src.session import SessionState

logger = structlog.get_logger()


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


async def single_turn_worker(
    session: SessionState,
    prompt: str,
    reply_message_id: str,
    api_client,
    semaphore: asyncio.Semaphore,
    timeout: float,
) -> None:
    """
    Process one Claude turn with concurrency control, timeout, and error handling.

    Locking order (Pitfall 4 deadlock prevention):
      1. semaphore — OUTER: global cap on concurrent Claude subprocesses (D-11)
      2. session.lock — INNER: per-session serialization (Pitfall 1)

    On success: calls update_card_content() with the response text.
    On asyncio.TimeoutError: calls send_error_card() with timeout message (CLAUDE-05).
    On any other Exception: logs error, calls send_error_card() (CLAUDE-04).
    Exceptions do NOT propagate — each worker is fully isolated.

    Args:
        session: SessionState with client and per-session lock.
        prompt: Formatted prompt string to send to Claude.
        reply_message_id: Message ID of the "thinking" card to update.
        api_client: Authenticated lark.Client instance for card updates.
        semaphore: Global asyncio.Semaphore capping concurrent tasks.
        timeout: Maximum seconds to wait for Claude response (CLAUDE_TIMEOUT).
    """
    async with semaphore:  # OUTER: global concurrency cap
        async with session.lock:  # INNER: per-session serialization
            try:
                result_text = await asyncio.wait_for(
                    _run_claude_turn(session.client, prompt),
                    timeout=timeout,
                )
                await update_card_content(api_client, reply_message_id, result_text)
            except asyncio.TimeoutError:
                logger.warning(
                    "claude_worker_timeout",
                    session_key=session.session_key,
                    timeout=timeout,
                )
                try:
                    await send_error_card(
                        api_client,
                        reply_message_id,
                        f"响应超时（>{timeout:.0f}s），请重试",
                    )
                except Exception as card_exc:
                    logger.error(
                        "claude_worker_error_card_failed",
                        session_key=session.session_key,
                        error=str(card_exc),
                    )
            except Exception as exc:
                logger.error(
                    "claude_worker_error",
                    session_key=session.session_key,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                try:
                    await send_error_card(
                        api_client,
                        reply_message_id,
                        f"处理出错：{exc}",
                    )
                except Exception as card_exc:
                    logger.error(
                        "claude_worker_error_card_failed",
                        session_key=session.session_key,
                        error=str(card_exc),
                    )
