"""CardStreamingManager — CardKit v1 element-level streaming updates (打字机效果).

Official API: PUT /open-apis/cardkit/v1/cards/{card_id}/elements/{element_id}/content
Requires: card JSON with streaming_mode=true and element with element_id.

Architecture: Two independent card elements updated via separate loops:
  - md_stream: main content (text + tool blocks). Only grows via prefix-extension
    so CardKit client renders smooth typing animation.
  - md_timer: timer status line. Updated at 1-second intervals independently,
    so timer changes never break the typing animation on main content.
"""
import asyncio
import json
import time
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = structlog.get_logger()

CARDKIT_BASE_URL = "https://open.feishu.cn/open-apis/cardkit/v1"
# Element IDs assigned in the card template (src/cards.py create_streaming_card)
STREAMING_ELEMENT_ID = "md_stream"
TIMER_ELEMENT_ID = "md_timer"


class CardStreamingManager:
    """
    Manages CardKit streaming updates for real-time card content.

    Two-element design:
    - md_stream: main content element. Text always prefix-extends for smooth typing.
    - md_timer: independent timer element. Updates every 1s without affecting typing.

    Flow:
    1. Create card with streaming_mode=true and two element_ids
    2. start() launches _flush_loop (content) + _timer_loop (timer) concurrently
    3. append_text/append_tool_use feed the content buffer
    4. finalize() sends final content + timer, closes streaming mode
    """

    def __init__(
        self,
        card_id: str,
        tenant_token: str,
        flush_interval: float = 0.4,
        think_start: float | None = None,
    ):
        self.card_id = card_id
        self.tenant_token = tenant_token
        self.flush_interval = flush_interval

        self._buffer: list[str] = []
        self._tool_blocks: list[str] = []
        self._full_text: str = ""
        self._sequence: int = 0
        self._dirty: bool = False
        self._client = httpx.AsyncClient(timeout=10.0)
        self._flush_task: asyncio.Task | None = None
        self._timer_task: asyncio.Task | None = None
        self._finalized = False
        self._think_start: float = think_start or time.monotonic()
        self._stream_start: float | None = None  # set by mark_stream_start()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def start(self) -> None:
        """Start both the content flush loop and the timer update loop."""
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._timer_task = asyncio.create_task(self._timer_loop())

    def mark_stream_start(self) -> None:
        """Mark transition from thinking to streaming. Called when Claude starts outputting."""
        if self._stream_start is None:
            self._stream_start = time.monotonic()

    async def append_text(self, text: str) -> None:
        """Append text token to buffer (flushed on timer)."""
        if self._finalized:
            return
        self._buffer.append(text)
        self._dirty = True

    async def append_tool_use(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Append tool use as compact one-liner."""
        if self._finalized:
            return
        self._tool_blocks.append(f"\U0001f527 `{tool_name}`")
        self._dirty = True

    async def append_tool_result(self, content: str | None, is_error: bool = False) -> None:
        """Update last tool block with result status (compact)."""
        if self._finalized:
            return
        if self._tool_blocks:
            last = self._tool_blocks[-1]
            status = "\u274c" if is_error else "\u2705"
            self._tool_blocks[-1] = f"{last} {status}"
        self._dirty = True

    async def finalize(self, final_text: str, is_error: bool = False) -> None:
        """Send final content + timer updates, then close streaming mode."""
        if self._finalized:
            return
        # Ensure stream_start is set for timer calculation
        self.mark_stream_start()

        # Cancel both background loops
        for task in (self._flush_task, self._timer_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Build final content — flush remaining buffer first
        self._full_text += "".join(self._buffer)
        self._buffer.clear()
        final = final_text or self._full_text
        if final:
            content = self._build_display_text(final, include_typing=False)
            await self._put_element(STREAMING_ELEMENT_ID, content)

        # Put final timer
        await self._put_element(TIMER_ELEMENT_ID, self._timer_line(final=True))

        # Wait for client to render final text before closing streaming mode
        await asyncio.sleep(1.0)

        # Close streaming mode so card can be forwarded/interacted with
        await self._close_streaming_mode()
        self._finalized = True
        await self._client.aclose()

    # ── Timer helpers ──────────────────────────────────────────────

    @staticmethod
    def _fmt_time(secs: int) -> str:
        """Format seconds as human-readable string."""
        if secs < 60:
            return f"{secs}s"
        mins = secs // 60
        remaining = secs % 60
        return f"{mins}m{remaining}s"

    def _is_thinking(self) -> bool:
        """True if still in think phase (mark_stream_start not called yet)."""
        return self._stream_start is None

    def _timer_line(self, final: bool = False) -> str:
        """Build timer status line (no markdown wrapping — just the backtick string)."""
        now = time.monotonic()
        if self._is_thinking():
            think_secs = int(now - self._think_start)
            return f"`\U0001f9e0 \u601d\u8003\u4e2d \u00b7 {self._fmt_time(think_secs)}`"
        think_secs = int(self._stream_start - self._think_start)
        stream_secs = int(now - self._stream_start)
        if final:
            return f"`\u2705 \u5b8c\u6210 \u00b7 \u601d\u8003 {self._fmt_time(think_secs)} \u00b7 \u8f93\u51fa {self._fmt_time(stream_secs)}`"
        return f"`\u23f1 \u601d\u8003 {self._fmt_time(think_secs)} \u00b7 \u8f93\u51fa {self._fmt_time(stream_secs)}`"

    # ── Display text builder (content element only, NO timer) ─────

    def _build_display_text(self, text: str, include_typing: bool = True) -> str:
        """Build display content for md_stream: tool blocks + text + optional typing indicator.

        Timer is NOT included — it lives in its own element (md_timer).
        """
        parts = []
        if self._tool_blocks:
            parts.append(" ".join(self._tool_blocks))
        if text:
            parts.append(text)
        if include_typing:
            if self._is_thinking():
                parts.append("**\u6b63\u5728\u601d\u8003\u4e2d...**")
            else:
                parts.append("_\u6b63\u5728\u8f93\u5165..._")
        return "\n\n".join(parts) if parts else "**\u6b63\u5728\u601d\u8003\u4e2d...**"

    # ── Content flush loop (md_stream only) ───────────────────────

    async def _flush_loop(self) -> None:
        """Periodic flush: merge buffer into full_text and PUT to md_stream.

        Only PUTs when content body actually changes. No timer logic here.
        Content always prefix-extends → smooth typing animation.
        """
        try:
            last_body = ""
            while True:
                await asyncio.sleep(self.flush_interval)

                # Merge buffer -> _full_text
                if self._buffer:
                    self._full_text += "".join(self._buffer)
                    self._buffer.clear()

                # Build body (tool blocks + text)
                body_parts = []
                if self._tool_blocks:
                    body_parts.append(" ".join(self._tool_blocks))
                if self._full_text:
                    body_parts.append(self._full_text)
                body = "\n\n".join(body_parts)

                # Only PUT when content actually changed
                if body != last_body or (not body and self._dirty):
                    content = self._build_display_text(self._full_text, include_typing=True)
                    await self._put_element(STREAMING_ELEMENT_ID, content)
                    last_body = body
                    self._dirty = False
        except asyncio.CancelledError:
            pass

    # ── Timer loop (md_timer only) ────────────────────────────────

    async def _timer_loop(self) -> None:
        """Update timer element every 1 second independently of content."""
        try:
            while True:
                await asyncio.sleep(1.0)
                timer_text = self._timer_line()
                await self._put_element(TIMER_ELEMENT_ID, timer_text)
        except asyncio.CancelledError:
            pass

    # ── Low-level PUT helpers ─────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _put_element(self, element_id: str, content: str) -> None:
        """PUT /cards/{card_id}/elements/{element_id}/content — shared sequence counter."""
        self._sequence += 1
        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/elements/{element_id}/content"
        body = {
            "content": content,
            "sequence": self._sequence,
        }
        resp = await self._client.put(url, headers=self._headers(), json=body)
        resp.raise_for_status()

    # Keep _put_content as alias for backward compat (tests, etc.)
    async def _put_content(self, content: str) -> None:
        """PUT content to the main streaming element (md_stream). Alias for _put_element."""
        await self._put_element(STREAMING_ELEMENT_ID, content)

    async def _update_header_title(self, title: str) -> None:
        """Update the card header title via CardKit settings API."""
        self._sequence += 1
        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/settings"
        body = {
            "settings": json.dumps({
                "header": {"title": {"tag": "plain_text", "content": title}},
            }),
            "sequence": self._sequence,
        }
        try:
            resp = await self._client.patch(url, headers=self._headers(), json=body)
            resp.raise_for_status()
            logger.debug("card_header_updated", card_id=self.card_id, title=title)
        except Exception as exc:
            logger.warning("card_header_update_failed", card_id=self.card_id, title=title, error=str(exc))

    async def _close_streaming_mode(self) -> None:
        """Close streaming mode via settings API so card can be forwarded/interacted."""
        self._sequence += 1
        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/settings"
        body = {
            "settings": json.dumps({"config": {"streaming_mode": False}}),
            "sequence": self._sequence,
        }
        try:
            resp = await self._client.patch(url, headers=self._headers(), json=body)
            resp.raise_for_status()
            logger.debug("streaming_mode_closed", card_id=self.card_id)
        except Exception as e:
            logger.warning("streaming_mode_close_failed", card_id=self.card_id, error=str(e))
