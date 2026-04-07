"""Config module — loads and validates environment variables at startup."""
import os
import sys

from dotenv import load_dotenv
import pydantic


class Config(pydantic.BaseModel):
    """Application configuration loaded from environment variables."""

    app_id: str
    app_secret: str
    log_level: str = "INFO"
    working_dir: str = "."
    claude_timeout: float = 3600.0
    max_concurrent_tasks: int = 5
    allowed_tools: list[str] = []
    admin_users: list[str] = []
    session_ttl: float = 3600.0
    log_format: str = "console"

    @classmethod
    def from_env(cls) -> "Config":
        """
        Load configuration from environment variables.

        Calls load_dotenv() to read .env file, then reads:
          - APP_ID (required)
          - APP_SECRET (required)
          - LOG_LEVEL (optional, default "INFO")
          - WORKING_DIR (optional, default ".")
          - CLAUDE_TIMEOUT (optional, default 3600.0)
          - MAX_CONCURRENT_TASKS (optional, default 5)
          - ALLOWED_TOOLS (optional, default [] — comma-separated list)
          - ADMIN_USERS (optional, default [] — comma-separated open_id list; empty = no restriction)
          - SESSION_TTL (optional, default 3600.0 — idle session expiry in seconds)
          - LOG_FORMAT (optional, default "console" — "console" or "JSON")

        Raises SystemExit(1) if any required variable is missing.
        """
        load_dotenv()
        try:
            app_id = os.environ["APP_ID"]
            app_secret = os.environ["APP_SECRET"]
        except KeyError as exc:
            print(f"FATAL: Missing required environment variable: {exc}", file=sys.stderr)
            sys.exit(1)

        # Parse ALLOWED_TOOLS: split by comma and filter empty strings
        allowed_tools_raw = os.environ.get("ALLOWED_TOOLS", "")
        allowed_tools = [t for t in allowed_tools_raw.split(",") if t] if allowed_tools_raw else []

        # Parse ADMIN_USERS: split by comma and filter empty strings
        admin_users_raw = os.environ.get("ADMIN_USERS", "")
        admin_users = [u.strip() for u in admin_users_raw.split(",") if u.strip()] if admin_users_raw else []

        try:
            return cls(
                app_id=app_id,
                app_secret=app_secret,
                log_level=os.environ.get("LOG_LEVEL", "INFO"),
                working_dir=os.environ.get("WORKING_DIR", "."),
                claude_timeout=float(os.environ.get("CLAUDE_TIMEOUT", "3600")),
                max_concurrent_tasks=int(os.environ.get("MAX_CONCURRENT_TASKS", "5")),
                allowed_tools=allowed_tools,
                admin_users=admin_users,
                session_ttl=float(os.environ.get("SESSION_TTL", "3600")),
                log_format=os.environ.get("LOG_FORMAT", "console"),
            )
        except pydantic.ValidationError as exc:
            print(f"FATAL: Configuration validation error: {exc}", file=sys.stderr)
            sys.exit(1)
