"""Feishu Bridge — entry point.

Starts the Feishu WebSocket long connection bot. On startup:
1. Loads and validates configuration from environment variables
2. Configures structured logging
3. Builds Feishu API client
4. Fetches bot's own open_id (needed for @mention detection, Pitfall 5)
5. Creates dedup cache and SessionManager with ClaudeAgentOptions
6. Wires event handler pipeline
7. Starts WebSocket client (blocks until process exit)
"""
import asyncio
import logging
import sys

# CRITICAL (Pitfall 2): Import lark_oapi.ws FIRST before creating any event loop.
# lark_oapi/ws/client.py captures asyncio.get_event_loop() at module import time.
# We must get the loop AFTER this import to ensure we reference the same loop.
import lark_oapi as lark
import lark_oapi.ws  # noqa: F401 — triggers loop capture at import time
from claude_agent_sdk import ClaudeAgentOptions

from src.config import Config
from src.dedup import DeduplicationCache
from src.handler import create_handler, create_card_action_handler
from src.session import SessionManager, session_cleanup_loop

import structlog
from structlog.contextvars import merge_contextvars


def get_bot_open_id(client: lark.Client) -> str:
    """
    Fetch the bot's own open_id at startup (CONN-03 prerequisite, Pitfall 5).

    The bot's open_id is needed to detect @mentions in group chats.
    Uses a raw API call to /bot/v3/info since lark-oapi 1.5.3 does not
    expose a dedicated bot.v3 resource.

    Args:
        client: Authenticated lark.Client instance.

    Returns:
        The bot's open_id string.

    Raises:
        RuntimeError: If the API call fails or returns no open_id.
    """
    request = lark.BaseRequest.builder() \
        .http_method(lark.HttpMethod.GET) \
        .uri("/open-apis/bot/v3/info") \
        .token_types({lark.AccessTokenType.TENANT}) \
        .build()
    resp = client.request(request)
    if not resp.success():
        raise RuntimeError(
            f"Failed to fetch bot info: {resp.code} {resp.msg}"
        )

    # resp.raw.content is bytes; parse as JSON
    import json
    data = json.loads(resp.raw.content)
    bot = data.get("bot", {})
    open_id = bot.get("open_id") or bot.get("app_open_id")
    if not open_id:
        raise RuntimeError(
            f"Bot open_id not found in response: {data}"
        )
    return open_id


def configure_structlog(log_level: str, log_format: str) -> None:
    """
    Configure structlog with merge_contextvars and optional JSON output.

    merge_contextvars MUST be first so event_id bound via bind_contextvars()
    in handler.py is injected into every log line (D-35, D-36).

    Args:
        log_level: Logging level string (e.g. "INFO", "DEBUG").
        log_format: "JSON" for JSONRenderer, anything else for ConsoleRenderer.
    """
    use_json = log_format.upper() == "JSON"
    processors = [
        merge_contextvars,  # MUST be first — injects event_id from contextvars
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer() if use_json else structlog.dev.ConsoleRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
    )


def main() -> None:
    """Main entry point — validates config, starts WS client."""
    # 1. Load config (CONN-05) — exits if required vars missing
    config = Config.from_env()

    # 2. Configure structlog (D-35, D-36)
    configure_structlog(config.log_level, config.log_format)
    logger = structlog.get_logger()

    # 3. Build lark API client
    api_client = (
        lark.Client.builder()
        .app_id(config.app_id)
        .app_secret(config.app_secret)
        .build()
    )

    # 4. Fetch bot open_id (CONN-03 prerequisite, Pitfall 5)
    bot_open_id = get_bot_open_id(api_client)
    logger.info("bot_info_fetched", bot_open_id=bot_open_id)

    # 5. Create dedup cache (CONN-02)
    dedup_cache = DeduplicationCache(max_size=1000, ttl_seconds=60)

    # 5b. Create Claude options and session manager (Phase 2)
    claude_options = ClaudeAgentOptions(
        permission_mode="acceptEdits",   # non-interactive, auto-approve edits
        cwd=config.working_dir,          # from WORKING_DIR env var
    )
    # Add allowed_tools only if configured (empty list = all tools allowed)
    if config.allowed_tools:
        claude_options.allowed_tools = config.allowed_tools

    semaphore = asyncio.Semaphore(config.max_concurrent_tasks)  # D-11, CONC-02
    session_manager = SessionManager(options=claude_options, semaphore=semaphore)
    logger.info(
        "session_manager_initialized",
        max_concurrent=config.max_concurrent_tasks,
        timeout=config.claude_timeout,
        working_dir=config.working_dir,
    )

    # 6. Get the event loop (AFTER lark_oapi.ws import at top of module — Pitfall 2)
    loop = asyncio.get_event_loop()

    # 6b. Start session TTL cleanup background task (SESS-05, D-37)
    cleanup_task = loop.create_task(
        session_cleanup_loop(session_manager, ttl_seconds=config.session_ttl)
    )
    logger.info("session_cleanup_started", session_ttl=config.session_ttl)

    # 7. Create sync handler via closure
    on_message = create_handler(loop, api_client, bot_open_id, dedup_cache, session_manager, config)

    # 7b. Create card action handler (INTER-03, D-28, D-29)
    on_card_action = create_card_action_handler()

    # 8. Build event dispatcher
    # Empty strings for encrypt_key/verification_token — WS handles auth at transport layer
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(on_card_action)  # INTER-03
        .build()
    )

    # 9. Build WS client (CONN-01, CONN-04: auto_reconnect=True)
    ws_client = lark.ws.Client(
        app_id=config.app_id,
        app_secret=config.app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG if config.log_level == "DEBUG" else lark.LogLevel.INFO,
        auto_reconnect=True,  # CONN-04
    )

    # 10. Start — blocks until process exit (CONN-01)
    logger.info("starting", app_id=config.app_id, bot_open_id=bot_open_id)
    ws_client.start()


if __name__ == "__main__":
    main()
