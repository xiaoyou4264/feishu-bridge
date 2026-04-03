"""CardStreamingManager — CardKit sequence-based streaming updates.

Flow:
1. create_streaming_card() creates CardKit card with streaming_mode=true
2. start() POSTs initial sequence to /cards/{card_id}/sequences
3. append_text/append_tool_use/append_tool_result accumulate content
4. Background flush loop PATCHes updated content every flush_interval
5. finalize(text) cancels flush loop, sends final content, closes client

Sequence API (httpx direct — not wrapped by lark-oapi):
- POST   /open-apis/cardkit/v1/cards/{card_id}/sequences
- PATCH  /open-apis/cardkit/v1/cards/{card_id}/sequences/{sequence_id}
- PATCH  /open-apis/cardkit/v1/cards/{card_id}/sequences/{sequence_id}  (with done=true)
"""
import asyncio
import time
import uuid
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_exception

logger = structlog.get_logger()

CARDKIT_BASE_URL = "https://open.feishu.cn/open-apis/cardkit/v1"
# The element_id used in cards with streaming_mode=true
STREAMING_ELEMENT_ID = "md_stream"


def _get_token(api_client) -> str:
    """Extract tenant_access_token from existing lark.Client config.

    Uses TokenManager so the token is cached and auto-refreshed by lark-oapi.
    """
    from lark_oapi.core.token import TokenManager
    return TokenManager.get_self_tenant_token(api_client._config)


def _build_card_content(text: str) -> dict:
    """Build card content dict for sequence API body.

    Returns a dict with CardKit v2 body structure:
    {"body": {"elements": [{"tag": "markdown", "content": text}]}}
    """
    return {
        "body": {
            "elements": [
                {"tag": "markdown", "content": text}
            ]
        }
    }


async def create_sequence(
    http: httpx.AsyncClient,
    api_client,
    card_id: str,
    seq_id: str,
    content: str,
) -> None:
    """POST /cardkit/v1/cards/{card_id}/sequences — create a new streaming sequence."""
    token = _get_token(api_client)
    url = f"{CARDKIT_BASE_URL}/cards/{card_id}/sequences"
    resp = await http.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json={"sequence_id": seq_id, "content": _build_card_content(content)},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(f"create_sequence failed: {data}")


async def update_sequence(
    http: httpx.AsyncClient,
    api_client,
    card_id: str,
    seq_id: str,
    content: str,
) -> None:
    """PATCH /cardkit/v1/cards/{card_id}/sequences/{sequence_id} — update content."""
    token = _get_token(api_client)
    url = f"{CARDKIT_BASE_URL}/cards/{card_id}/sequences/{seq_id}"
    resp = await http.patch(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json={"content": _build_card_content(content)},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(f"update_sequence failed: {data}")


async def finish_sequence(
    http: httpx.AsyncClient,
    api_client,
    card_id: str,
    seq_id: str,
) -> None:
    """PATCH /cardkit/v1/cards/{card_id}/sequences/{sequence_id}/finish — close sequence."""
    token = _get_token(api_client)
    url = f"{CARDKIT_BASE_URL}/cards/{card_id}/sequences/{seq_id}/finish"
    resp = await http.patch(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json={},
    )
    resp.raise_for_status()


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if exception is a 429 rate limit error."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


@retry(
    retry=retry_if_exception(_is_rate_limit_error),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def _update_sequence_with_retry(
    http: httpx.AsyncClient,
    api_client,
    card_id: str,
    seq_id: str,
    content: str,
) -> None:
    """update_sequence wrapped with tenacity retry on 429 (D-23)."""
    await update_sequence(http, api_client, card_id, seq_id, content)


class CardStreamingManager:
    """
    Manages CardKit streaming sequence lifecycle for real-time card content.

    Sequence lifecycle:
    1. start() — POST create sequence (with initial content)
    2. _flush_buffer() — PATCH update sequence (repeat at flush_interval)
    3. _finish_sequence(text) — PATCH with done=True (final content)
    4. finalize(text) — cancels loop, calls _finish_sequence, closes client

    Key invariants:
    - sequence_id is caller-generated (UUID) per D-22
    - Typing indicator appended during streaming, removed on finalize (D-26/D-27)
    - Tool blocks shown before text (D-24/D-25)
    - 429 retried via tenacity (D-23)
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
        self.sequence_id: str = uuid.uuid4().hex[:16]

        self._buffer: list[str] = []
        self._tool_blocks: list[str] = []
        self._full_text: str = ""
        self._sequence_created: bool = False
        self._finalized: bool = False
        self._client = httpx.AsyncClient(timeout=10.0)
        self._flush_task: asyncio.Task | None = None
        self._start_time: float = time.monotonic()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _elapsed(self) -> str:
        """Format elapsed time since start."""
        secs = int(time.monotonic() - self._start_time)
        if secs < 60:
            return f"{secs}s"
        mins = secs // 60
        remaining = secs % 60
        return f"{mins}m{remaining}s"

    def _build_display_text(self, include_typing: bool = True) -> str:
        """Build display text from tool blocks + buffer, with optional typing indicator."""
        parts = []
        if self._tool_blocks:
            parts.append("\n".join(self._tool_blocks))
        current_text = self._full_text + "".join(self._buffer)
        if current_text:
            parts.append(current_text)
        if include_typing:
            parts.append(f"_正在输入..._\n\n---\n\n`⏱ {self._elapsed()}`")
        content = "\n\n".join(parts) if parts else (f"_正在输入..._\n\n---\n\n`⏱ {self._elapsed()}`" if include_typing else "")
        return content

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _create_sequence(self) -> None:
        """POST initial sequence to /cards/{card_id}/sequences."""
        content_text = self._build_display_text(include_typing=True)
        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/sequences"
        body = {
            "sequence_id": self.sequence_id,
            "content": _build_card_content(content_text),
        }
        resp = await self._client.post(url, headers=self._headers(), json=body)
        resp.raise_for_status()
        self._sequence_created = True

    async def start(self) -> None:
        """Create initial sequence and start background flush loop."""
        await self._create_sequence()
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def append_text(self, text: str) -> None:
        """Append text token to buffer (flushed on timer)."""
        if self._finalized:
            return
        self._buffer.append(text)

    async def append_tool_use(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Append tool use as compact one-liner with tool name and key input."""
        if self._finalized:
            return
        # Extract brief summary from input
        summary = ""
        if isinstance(tool_input, dict):
            for key in ("command", "content", "file_path", "pattern", "description", "file"):
                if key in tool_input:
                    val = str(tool_input[key])
                    summary = val[:80] + ("..." if len(val) > 80 else "")
                    break
        if summary:
            block = f"🔧 {tool_name}: `{summary}`"
        else:
            block = f"🔧 {tool_name}"
        self._tool_blocks.append(block)

    async def append_tool_result(self, content: str | None, is_error: bool = False) -> None:
        """Append tool result as a new block with status indicator."""
        if self._finalized:
            return
        if is_error:
            summary = (str(content)[:100] if content else "")
            block = f"❌ Error: {summary}"
        else:
            summary = (str(content)[:100] if content else "")
            block = f"✅ Done: {summary}"
        self._tool_blocks.append(block)

    async def _flush_buffer(self) -> None:
        """PATCH sequence with accumulated buffer content + typing indicator."""
        if not self._sequence_created:
            return
        # Merge buffer into full_text
        if self._buffer:
            self._full_text += "".join(self._buffer)
            self._buffer.clear()
        content_text = self._build_display_text(include_typing=True)
        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/sequences/{self.sequence_id}"
        body = {
            "content": _build_card_content(content_text),
        }
        resp = await self._client.patch(url, headers=self._headers(), json=body)
        resp.raise_for_status()

    async def _finish_sequence(self, final_text: str) -> None:
        """PATCH sequence with done=True and final content (no typing indicator)."""
        if self._sequence_created:
            # Merge remaining buffer
            if self._buffer:
                self._full_text += "".join(self._buffer)
                self._buffer.clear()
            # Use provided final_text if given, otherwise use accumulated text
            text = final_text if final_text else self._full_text
            # Build content from tool blocks + final text
            parts = []
            if self._tool_blocks:
                parts.append("\n".join(self._tool_blocks))
            if text:
                # Append elapsed time to final content
                elapsed = self._elapsed()
                text_with_time = f"{text}\n\n---\n\n`✅ 完成 · ⏱ {elapsed}`"
                parts.append(text_with_time)
            content_text = "\n\n".join(parts) if parts else text
            url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/sequences/{self.sequence_id}"
            body = {
                "content": _build_card_content(content_text),
                "done": True,
            }
            resp = await self._client.patch(url, headers=self._headers(), json=body)
            resp.raise_for_status()

    async def _flush_loop(self) -> None:
        """Periodic flush: patch sequence with updated buffer content."""
        try:
            while True:
                await asyncio.sleep(self.flush_interval)
                if self._buffer:
                    try:
                        await self._flush_buffer()
                    except Exception as exc:
                        logger.warning("card_flush_error", card_id=self.card_id, error=str(exc))
        except asyncio.CancelledError:
            pass

    async def finalize(self, final_text: str) -> None:
        """Cancel flush loop, send final content, close httpx client."""
        if self._finalized:
            return

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        try:
            await self._finish_sequence(final_text)
        except Exception as exc:
            logger.error("card_finalize_error", card_id=self.card_id, error=str(exc))

        self._finalized = True
        await self._client.aclose()
