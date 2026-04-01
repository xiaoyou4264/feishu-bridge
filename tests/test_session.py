"""Unit tests for SessionManager and SessionState (session.py)."""
import asyncio
import time
import types

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def make_mock_client():
    """Create a mock ClaudeSDKClient with async connect/disconnect."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    return client


def make_mock_api_client(user_name="张三", success=True):
    """Create a mock lark api_client with contact.v3.user.aget()."""
    api_client = MagicMock()

    user = MagicMock()
    user.name = user_name

    data = MagicMock()
    data.user = user

    resp = MagicMock()
    resp.success.return_value = success
    resp.data = data

    api_client.contact = MagicMock()
    api_client.contact.v3 = MagicMock()
    api_client.contact.v3.user = MagicMock()
    api_client.contact.v3.user.aget = AsyncMock(return_value=resp)

    return api_client


# ---------------------------------------------------------------------------
# get_session_key tests
# ---------------------------------------------------------------------------

class TestGetSessionKey:
    def test_p2p_session_key(self):
        """P2P session key is sender's open_id (SESS-01)."""
        from src.session import get_session_key
        key = get_session_key(chat_type="p2p", sender_open_id="ou_abc", chat_id="oc_xyz")
        assert key == "ou_abc"

    def test_group_session_key(self):
        """Group session key is chat_id (SESS-02)."""
        from src.session import get_session_key
        key = get_session_key(chat_type="group", sender_open_id="ou_abc", chat_id="oc_xyz")
        assert key == "oc_xyz"


# ---------------------------------------------------------------------------
# SessionState tests
# ---------------------------------------------------------------------------

class TestSessionState:
    def test_session_has_lock(self):
        """SessionState has an asyncio.Lock attribute."""
        from src.session import SessionState
        client = make_mock_client()
        session = SessionState(session_key="key1", client=client)
        assert isinstance(session.lock, asyncio.Lock)

    def test_session_has_name_cache(self):
        """SessionState has a name_cache dict attribute."""
        from src.session import SessionState
        client = make_mock_client()
        session = SessionState(session_key="key1", client=client)
        assert isinstance(session.name_cache, dict)

    def test_session_has_last_activity(self):
        """SessionState has a last_activity float attribute."""
        from src.session import SessionState
        client = make_mock_client()
        before = time.time()
        session = SessionState(session_key="key1", client=client)
        after = time.time()
        assert before <= session.last_activity <= after


# ---------------------------------------------------------------------------
# SessionManager tests
# ---------------------------------------------------------------------------

class TestSessionManager:
    @pytest.mark.asyncio
    async def test_get_or_create_new_session(self):
        """get_or_create creates a new SessionState with ClaudeSDKClient and calls connect()."""
        from src.session import SessionManager
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(permission_mode="acceptEdits", cwd=".")
        semaphore = asyncio.Semaphore(5)
        manager = SessionManager(options=options, semaphore=semaphore)

        mock_client = make_mock_client()

        with patch("src.session.ClaudeSDKClient", return_value=mock_client) as MockClient:
            session = await manager.get_or_create("key1")

        MockClient.assert_called_once_with(options=options)
        mock_client.connect.assert_awaited_once()
        assert session.session_key == "key1"
        assert session.client is mock_client

    @pytest.mark.asyncio
    async def test_get_or_create_existing_session(self):
        """Calling get_or_create with same key twice returns the SAME SessionState instance (CLAUDE-03)."""
        from src.session import SessionManager
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(permission_mode="acceptEdits", cwd=".")
        semaphore = asyncio.Semaphore(5)
        manager = SessionManager(options=options, semaphore=semaphore)

        mock_client = make_mock_client()

        with patch("src.session.ClaudeSDKClient", return_value=mock_client):
            session1 = await manager.get_or_create("key1")
            session2 = await manager.get_or_create("key1")

        assert session1 is session2
        # connect() should only be called once (not twice)
        mock_client.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_destroy_session(self):
        """destroy() calls client.disconnect() and removes session from dict (SESS-03)."""
        from src.session import SessionManager
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(permission_mode="acceptEdits", cwd=".")
        semaphore = asyncio.Semaphore(5)
        manager = SessionManager(options=options, semaphore=semaphore)

        mock_client = make_mock_client()

        with patch("src.session.ClaudeSDKClient", return_value=mock_client):
            await manager.get_or_create("key1")

        await manager.destroy("key1")

        mock_client.disconnect.assert_awaited_once()
        # Session should be removed from internal dict
        assert "key1" not in manager._sessions

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_session(self):
        """destroy() on non-existent key does NOT raise."""
        from src.session import SessionManager
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(permission_mode="acceptEdits", cwd=".")
        semaphore = asyncio.Semaphore(5)
        manager = SessionManager(options=options, semaphore=semaphore)

        # Should not raise
        await manager.destroy("no_such_key")

    @pytest.mark.asyncio
    async def test_destroy_tolerates_disconnect_error(self):
        """destroy() tolerates errors during client.disconnect()."""
        from src.session import SessionManager
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(permission_mode="acceptEdits", cwd=".")
        semaphore = asyncio.Semaphore(5)
        manager = SessionManager(options=options, semaphore=semaphore)

        mock_client = make_mock_client()
        mock_client.disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))

        with patch("src.session.ClaudeSDKClient", return_value=mock_client):
            await manager.get_or_create("key1")

        # Should not raise even though disconnect fails
        await manager.destroy("key1")
        assert "key1" not in manager._sessions

    def test_semaphore_property(self):
        """SessionManager exposes semaphore via property."""
        from src.session import SessionManager
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(permission_mode="acceptEdits", cwd=".")
        semaphore = asyncio.Semaphore(5)
        manager = SessionManager(options=options, semaphore=semaphore)

        assert manager.semaphore is semaphore

    @pytest.mark.asyncio
    async def test_get_or_create_updates_last_activity(self):
        """get_or_create updates last_activity timestamp on each call."""
        from src.session import SessionManager
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions(permission_mode="acceptEdits", cwd=".")
        semaphore = asyncio.Semaphore(5)
        manager = SessionManager(options=options, semaphore=semaphore)

        mock_client = make_mock_client()

        with patch("src.session.ClaudeSDKClient", return_value=mock_client):
            session1 = await manager.get_or_create("key1")
            t1 = session1.last_activity
            await asyncio.sleep(0.01)  # small delay to ensure timestamp changes
            session2 = await manager.get_or_create("key1")
            t2 = session2.last_activity

        assert t2 >= t1


# ---------------------------------------------------------------------------
# get_display_name tests
# ---------------------------------------------------------------------------

class TestGetDisplayName:
    @pytest.mark.asyncio
    async def test_get_display_name_fetches_from_api(self):
        """First call to get_display_name fetches from contact API and returns name."""
        from src.session import get_display_name

        api_client = make_mock_api_client(user_name="张三")
        name_cache: dict = {}

        name = await get_display_name(api_client, "ou_abc123", name_cache)

        assert name == "张三"
        api_client.contact.v3.user.aget.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_display_name_caches(self):
        """Second call with same open_id does NOT call contact API (uses cache)."""
        from src.session import get_display_name

        api_client = make_mock_api_client(user_name="张三")
        name_cache: dict = {}

        name1 = await get_display_name(api_client, "ou_abc123", name_cache)
        name2 = await get_display_name(api_client, "ou_abc123", name_cache)

        assert name1 == "张三"
        assert name2 == "张三"
        # API called only once
        api_client.contact.v3.user.aget.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_display_name_fallback_on_error(self):
        """When contact API fails, returns last 8 chars of open_id as fallback (Pitfall 6)."""
        from src.session import get_display_name

        api_client = make_mock_api_client(success=False)
        name_cache: dict = {}

        open_id = "ou_abcdefghijkl"
        name = await get_display_name(api_client, open_id, name_cache)

        assert name == open_id[-8:]

    @pytest.mark.asyncio
    async def test_get_display_name_fallback_is_cached(self):
        """Fallback value is cached so API is not retried on subsequent calls."""
        from src.session import get_display_name

        api_client = make_mock_api_client(success=False)
        name_cache: dict = {}

        open_id = "ou_abcdefghijkl"
        await get_display_name(api_client, open_id, name_cache)
        await get_display_name(api_client, open_id, name_cache)

        # API called only once
        api_client.contact.v3.user.aget.assert_awaited_once()


# ---------------------------------------------------------------------------
# format_prompt tests
# ---------------------------------------------------------------------------

class TestFormatPrompt:
    def test_format_prompt_group_has_prefix(self):
        """Group messages get [display_name]: prefix (D-14)."""
        from src.session import format_prompt
        result = format_prompt("hello world", chat_type="group", display_name="张三")
        assert result == "[张三]: hello world"

    def test_format_prompt_p2p_no_prefix(self):
        """P2P messages have no prefix (D-15)."""
        from src.session import format_prompt
        result = format_prompt("hello world", chat_type="p2p", display_name="张三")
        assert result == "hello world"

    def test_format_prompt_group_no_display_name(self):
        """Group message with no display_name returns text unchanged."""
        from src.session import format_prompt
        result = format_prompt("hello world", chat_type="group", display_name=None)
        assert result == "hello world"

    def test_format_prompt_empty_text(self):
        """Empty text with group prefix still returns [name]: ."""
        from src.session import format_prompt
        result = format_prompt("", chat_type="group", display_name="Alice")
        assert result == "[Alice]: "
