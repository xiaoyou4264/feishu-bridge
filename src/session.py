"""Session management for Feishu bridge — per-conversation Claude SDK client lifecycle.

Provides:
- SessionState: dataclass holding a ClaudeSDKClient, asyncio.Lock, name cache, etc.
- SessionManager: manages a dict of SessionState keyed by session_key
- get_session_key(): maps chat event to P2P (open_id) or group (chat_id) key
- get_display_name(): fetches user display name from Feishu contact API (with cache + fallback)
- format_prompt(): prepends [name]: prefix for group messages (D-14, D-15)
"""
import asyncio
import time
from dataclasses import dataclass, field

import structlog
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from lark_oapi.api.contact.v3 import GetUserRequest

logger = structlog.get_logger()


@dataclass
class SessionState:
    """
    Per-conversation state: one ClaudeSDKClient plus concurrency control.

    session_key: P2P → sender open_id; group → chat_id
    client: the persistent ClaudeSDKClient for multi-turn context (CLAUDE-03)
    lock: serializes concurrent query() calls on this client (Pitfall 1)
    name_cache: open_id → display_name, to avoid repeated contact API calls
    last_activity: unix timestamp of most recent access
    """

    session_key: str
    client: ClaudeSDKClient
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    name_cache: dict[str, str] = field(default_factory=dict)
    last_activity: float = field(default_factory=time.time)


class SessionManager:
    """
    Manages a dict[session_key, SessionState].

    Each unique session_key gets exactly one ClaudeSDKClient which is
    reused across turns to preserve multi-turn context (D-08, CLAUDE-03).
    """

    def __init__(self, options: ClaudeAgentOptions, semaphore: asyncio.Semaphore):
        self._sessions: dict[str, SessionState] = {}
        self._options = options
        self._semaphore = semaphore

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """Exposes the global concurrency semaphore for workers."""
        return self._semaphore

    async def get_or_create(self, session_key: str) -> SessionState:
        """
        Return existing SessionState or create a new one.

        If no session exists for session_key, creates a ClaudeSDKClient,
        calls connect(), and stores the SessionState. Updates last_activity
        on every call.

        Args:
            session_key: P2P open_id or group chat_id.

        Returns:
            The SessionState for this session_key.
        """
        if session_key not in self._sessions:
            client = ClaudeSDKClient(options=self._options)
            await client.connect()
            self._sessions[session_key] = SessionState(
                session_key=session_key,
                client=client,
            )
            logger.info("session_created", session_key=session_key)

        self._sessions[session_key].last_activity = time.time()
        return self._sessions[session_key]

    async def destroy(self, session_key: str) -> None:
        """
        Disconnect and remove session.

        If session_key is not found, does nothing. Disconnect errors are
        tolerated (cleanup is best-effort) to support /new command (SESS-03).

        Args:
            session_key: P2P open_id or group chat_id.
        """
        if session_key not in self._sessions:
            return

        state = self._sessions.pop(session_key)
        try:
            await state.client.disconnect()
        except Exception as exc:
            logger.warning(
                "session_disconnect_error",
                session_key=session_key,
                error=str(exc),
            )

        logger.info("session_destroyed", session_key=session_key)


def get_session_key(
    chat_type: str,
    sender_open_id: str,
    chat_id: str,
) -> str:
    """
    Compute session key from message metadata.

    P2P: isolated per-user → use sender's open_id (SESS-01)
    Group: shared per-chat → use chat_id (SESS-02)

    Args:
        chat_type: "p2p" or "group" (from message.chat_type)
        sender_open_id: Sender's open_id
        chat_id: Chat/group ID

    Returns:
        The session key string.
    """
    if chat_type == "p2p":
        return sender_open_id
    return chat_id


async def get_display_name(
    api_client,
    open_id: str,
    name_cache: dict[str, str],
) -> str:
    """
    Fetch user display name from Feishu contact API.

    Checks name_cache first. On cache miss, calls contact.v3.user.aget().
    If API fails, uses last 8 chars of open_id as fallback (Pitfall 6).
    Result is always cached to avoid repeated API calls.

    Args:
        api_client: Authenticated lark.Client instance.
        open_id: User's open_id.
        name_cache: Per-session cache dict (open_id -> display_name).

    Returns:
        Display name string.
    """
    if open_id in name_cache:
        return name_cache[open_id]

    try:
        request = (
            GetUserRequest.builder()
            .user_id(open_id)
            .user_id_type("open_id")
            .build()
        )
        resp = await api_client.contact.v3.user.aget(request)
        if resp.success():
            name = resp.data.user.name
        else:
            logger.warning(
                "get_display_name_api_failed",
                open_id=open_id,
                code=getattr(resp, "code", None),
            )
            name = open_id[-8:]
    except Exception as exc:
        logger.warning(
            "get_display_name_exception",
            open_id=open_id,
            error=str(exc),
        )
        name = open_id[-8:]

    name_cache[open_id] = name
    return name


def format_prompt(
    text: str,
    chat_type: str,
    display_name: str | None = None,
) -> str:
    """
    Format message text for Claude, injecting sender prefix in group chats.

    Group: "[display_name]: text" (D-14)
    P2P: "text" (D-15)

    Args:
        text: Raw message text.
        chat_type: "p2p" or "group".
        display_name: Sender display name (used only for group chats).

    Returns:
        Formatted prompt string.
    """
    if chat_type == "group" and display_name is not None:
        return f"[{display_name}]: {text}"
    return text
