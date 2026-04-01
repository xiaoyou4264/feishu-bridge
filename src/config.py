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

    @classmethod
    def from_env(cls) -> "Config":
        """
        Load configuration from environment variables.

        Calls load_dotenv() to read .env file, then reads:
          - APP_ID (required)
          - APP_SECRET (required)
          - LOG_LEVEL (optional, default "INFO")
          - WORKING_DIR (optional, default ".")

        Raises SystemExit(1) if any required variable is missing.
        """
        load_dotenv()
        try:
            app_id = os.environ["APP_ID"]
            app_secret = os.environ["APP_SECRET"]
        except KeyError as exc:
            print(f"FATAL: Missing required environment variable: {exc}", file=sys.stderr)
            sys.exit(1)

        try:
            return cls(
                app_id=app_id,
                app_secret=app_secret,
                log_level=os.environ.get("LOG_LEVEL", "INFO"),
                working_dir=os.environ.get("WORKING_DIR", "."),
            )
        except pydantic.ValidationError as exc:
            print(f"FATAL: Configuration validation error: {exc}", file=sys.stderr)
            sys.exit(1)
