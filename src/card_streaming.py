"""CardStreamingManager — encapsulates CardKit sequence lifecycle for streaming updates."""
import asyncio
import json
import uuid
from typing import Any

import httpx
import structlog
from lark_oapi.core.token import TokenManager
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = structlog.get_logger()

CARDKIT_BASE_URL = "https://open.feishu.cn/open-apis/cardkit/v1"


class CardStreamingManager:
    """
    Manages CardKit sequence lifecycle for streaming card updates.

    Encapsulates:
    - Sequence creation (POST /cards/{card_id}/sequences)
    - Batched updates (PATCH /cards/{card_id}/sequences/{sequence_id})
    - Typing indicator management
    - Tool use/result rendering
    - Sequence finalization (PATCH with done=true)
    - 429 retry with exponential backoff
    """

    def __init__(
        self,
        card_id: str,
        tenant_token: str,
        flush_interval: float = 0.4,
    ):
        """
        Initialize streaming manager.

        Args:
            card_id: CardKit card_id from create_streaming_card()
            tenant_token: Feishu tenant access token
            flush_interval: Batch flush interval in seconds (default 400ms)
        """
        self.card_id = card_id
        self.tenant_token = tenant_token
        self.flush_interval = flush_interval
        self.sequence_id = str(uuid.uuid4())

        self._buffer: list[str] = []
        self._tool_blocks: list[str] = []
        self._client = httpx.AsyncClient(timeout=10.0)
        self._flush_task: asyncio.Task | None = None
        self._sequence_created = False
        self._finalized = False

    async def start(self) -> None:
        """Create sequence and start flush timer."""
        await self._create_sequence()
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def append_text(self, text: str) -> None:
        """Append text token to buffer (will be flushed on timer)."""
        if self._finalized:
            return
        self._buffer.append(text)

    async def append_tool_use(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Append tool use block (rendered as markdown section)."""
        if self._finalized:
            return
        input_summary = json.dumps(tool_input, ensure_ascii=False)[:100]
        self._tool_blocks.append(f"**🔧 {tool_name}**\n```\n{input_summary}\n```")

    async def append_tool_result(self, content: str | None, is_error: bool = False) -> None:
        """Append tool result block with content summary."""
        if self._finalized:
            return
        status = "❌ Error" if is_error else "✅ Done"
        summary = (content or "")[:200] if content else "(no output)"
        self._tool_blocks.append(f"{status}\n```\n{summary}\n```")

    async def finalize(self, final_text: str) -> None:
        """Finish sequence with final text (removes typing indicator)."""
        if self._finalized:
            return

        # Cancel flush loop
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Final update without typing indicator
        await self._finish_sequence(final_text)
        self._finalized = True
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _create_sequence(self) -> None:
        """Create CardKit sequence (POST /cards/{card_id}/sequences)."""
        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/sequences"
        headers = {
            "Authorization": f"Bearer {self.tenant_token}",
            "Content-Type": "application/json",
        }
        body = {
            "sequence_id": self.sequence_id,
            "streaming_config": {
                "print_step": True,
                "print_frequency_ms": int(self.flush_interval * 1000),
            },
        }

        resp = await self._client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        self._sequence_created = True
        logger.debug("sequence_created", card_id=self.card_id, sequence_id=self.sequence_id)

    async def _flush_loop(self) -> None:
        """Periodic flush loop (runs until finalize)."""
        try:
            while True:
                await asyncio.sleep(self.flush_interval)
                if self._buffer or self._tool_blocks:
                    await self._flush_buffer()
        except asyncio.CancelledError:
            pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _flush_buffer(self) -> None:
        """Flush accumulated text + tool blocks to CardKit (PATCH sequence)."""
        if not self._sequence_created:
            return

        # Build content: tool blocks + text + typing indicator
        content_parts = []
        if self._tool_blocks:
            content_parts.extend(self._tool_blocks)
        if self._buffer:
            content_parts.append("".join(self._buffer))
        content_parts.append("\n\n_正在输入..._")

        content = "\n\n".join(content_parts)

        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/sequences/{self.sequence_id}"
        headers = {
            "Authorization": f"Bearer {self.tenant_token}",
            "Content-Type": "application/json",
        }
        body = {
            "content": {
                "schema": "2.0",
                "body": {
                    "elements": [
                        {"tag": "markdown", "content": content}
                    ]
                },
            }
        }

        resp = await self._client.patch(url, headers=headers, json=body)
        resp.raise_for_status()

        # Clear buffer after successful flush
        self._buffer.clear()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True,
    )
    async def _finish_sequence(self, final_text: str) -> None:
        """Finish sequence with final content (PATCH with done=true)."""
        if not self._sequence_created:
            return

        # Build final content: tool blocks + final text (no typing indicator)
        content_parts = []
        if self._tool_blocks:
            content_parts.extend(self._tool_blocks)
        content_parts.append(final_text)

        content = "\n\n".join(content_parts)

        url = f"{CARDKIT_BASE_URL}/cards/{self.card_id}/sequences/{self.sequence_id}"
        headers = {
            "Authorization": f"Bearer {self.tenant_token}",
            "Content-Type": "application/json",
        }
        body = {
            "content": {
                "schema": "2.0",
                "body": {
                    "elements": [
                        {"tag": "markdown", "content": content}
                    ]
                },
            },
            "done": True,
        }

        resp = await self._client.patch(url, headers=headers, json=body)
        resp.raise_for_status()
        logger.debug("sequence_finished", card_id=self.card_id, sequence_id=self.sequence_id)
