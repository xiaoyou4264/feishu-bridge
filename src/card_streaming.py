"""CardStreamingManager — CardKit v1 element-level streaming updates (打字机效果).

Official API: PUT /open-apis/cardkit/v1/cards/{card_id}/elements/{element_id}/content
Requires: card JSON with streaming_mode=true and element with element_id.
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
# The element_id we assign to the markdown element in the card template
STREAMING_ELEMENT_ID = "md_stream"


class CardStreamingManager:
    """
    Manages CardKit streaming updates for real-time card content.

    Flow:
    1. Create card with streaming_mode=true and element_id on the markdown element
    2. PUT /cards/{card_id}/elements/{element_id}/content with incremental text + sequence
    3. When done, send final full text (飞书 auto-detects prefix match for typing effect)

    Key rule: new text must be a prefix-extension of old text for typing effect.
    If prefix differs, full text replaces instantly (no animation).
    """

    def __init__(
        self,
        card_id: str,
        tenant_token: str,
        flush_interval: float = 0.4,
    ):
        self.card_id = card_id
        self.tenant_token = tenant_token
        self.flush_interval = flush_interval
        self.element_id = STREAMING_ELEMENT_ID

        self._buffer: list[str] = []
        self._tool_blocks: list[str] = []
        self._full_text: str = ""
        self._sequence: int = 0
        self._dirty: bool = False
        self._client = httpx.AsyncClient(timeout=10.0)
        self._flush_task: asyncio.Task | None = None
        self._finalized = False
        self._start_time: float = time.monotonic()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def start(self) -> None:
        """Start the flush timer loop."""
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def append_text(self, text: str) -> None:
        """Append text token to buffer (flushed on timer)."""
        if self._finalized:
            return
        self._buffer.append(text)

    async def append_tool_use(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Append tool use as compact one-liner."""
        if self._finalized:
            return
        # Compact: just tool name, no verbose input
        self._tool_blocks.append(f"🔧 `{tool_name}`")
        self._dirty = True

    async def append_tool_result(self, content: str | None, is_error: bool = False) -> None:
        """Update last tool block with result status (compact)."""
        if self._finalized:
            return
        if self._tool_blocks:
            # Replace last tool entry with result status on same line
            last = self._tool_blocks[-1]
            status = "❌" if is_error else "✅"
            self._tool_blocks[-1] = f"{last} {status}"
        self._dirty = True

    async def finalize(self, final_text: str) -> None:
        """Send final content update (no typing indicator)."""
        if self._finalized:
            return

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Build final content — flush remaining buffer first
        self._full_text += "".join(self._buffer)
        self._buffer.clear()
        final = final_text or self._full_text
        if final:
            # Append elapsed time to final content
            final_with_time = f"{final}\n\n---\n\n`✅ 完成 · ⏱ {self._elapsed()}`"
            content = self._build_display_text(final_with_time, include_typing=False)
            await self._put_content(content)

        # Wait for client to render final text before closing streaming mode
        await asyncio.sleep(1.0)

        # Close streaming mode so card can be forwarded/interacted with
        await self._close_streaming_mode()
        self._finalized = True
        await self._client.aclose()

    def _elapsed(self) -> str:
        """Format elapsed time since start (integer seconds)."""
        secs = int(time.monotonic() - self._start_time)
        if secs < 60:
            return f"{secs}s"
        mins = secs // 60
        remaining = secs % 60
        return f"{mins}m{remaining}s"

    def _build_display_text(self, text: str, include_typing: bool = True) -> str:
        """Build display content: compact tool line + text + optional typing/timer indicator."""
        parts = []
        if self._tool_blocks:
            parts.append(" ".join(self._tool_blocks))
        if text:
            parts.append(text)
        if include_typing:
            parts.append(f"_正在输入..._\n\n---\n\n`⏱ {self._elapsed()}`")
        return "\n\n".join(parts) if parts else f"_正在输入..._\n\n---\n\n`⏱ {self._elapsed()}`"

    async def _flush_loop(self) -> None:
        """Periodic flush: merge buffer into full_text and PUT update. Always update timer."""
        try:
            last_put_text = ""
            while True:
                await asyncio.sleep(self.flush_interval)
                if self._buffer:
                    self._full_text += "".join(self._buffer)
                    self._buffer.clear()
                    self._dirty = False
                # Always rebuild content (timer changes every tick)
                content = self._build_display_text(self._full_text, include_typing=True)
                # Only PUT if content actually changed (text or timer second changed)
                if content != last_put_text:
                    await self._put_content(content)
                    last_put_text = content
        except asyncio.CancelledError:
            pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _put_content(self, content: str) -> None:
        """PUT /cards/{card_id}/elements/{element_id}/content"""
        self._sequence += 1
        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/elements/{self.element_id}/content"
        body = {
            "content": content,
            "sequence": self._sequence,
        }
        resp = await self._client.put(url, headers=self._headers(), json=body)
        resp.raise_for_status()

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
