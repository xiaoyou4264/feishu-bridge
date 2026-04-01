"""Tests for src/config.py — Config.from_env() validation."""
import os
import sys
import pytest


class TestConfigFromEnv:
    def test_from_env_succeeds_with_required_vars(self, monkeypatch):
        """Config.from_env() succeeds when APP_ID and APP_SECRET are set."""
        monkeypatch.setenv("APP_ID", "test_id")
        monkeypatch.setenv("APP_SECRET", "test_secret")
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("WORKING_DIR", raising=False)

        from src.config import Config
        cfg = Config.from_env()
        assert cfg.app_id == "test_id"
        assert cfg.app_secret == "test_secret"

    def test_from_env_raises_on_missing_app_id(self, monkeypatch):
        """Config.from_env() calls sys.exit(1) when APP_ID is missing."""
        monkeypatch.delenv("APP_ID", raising=False)
        monkeypatch.setenv("APP_SECRET", "test_secret")

        from src.config import Config
        with pytest.raises(SystemExit) as exc_info:
            Config.from_env()
        assert exc_info.value.code == 1

    def test_from_env_raises_on_missing_app_secret(self, monkeypatch):
        """Config.from_env() calls sys.exit(1) when APP_SECRET is missing."""
        monkeypatch.setenv("APP_ID", "test_id")
        monkeypatch.delenv("APP_SECRET", raising=False)

        from src.config import Config
        with pytest.raises(SystemExit) as exc_info:
            Config.from_env()
        assert exc_info.value.code == 1

    def test_from_env_defaults_log_level_to_info(self, monkeypatch):
        """Config.from_env() uses LOG_LEVEL='INFO' when not set."""
        monkeypatch.setenv("APP_ID", "test_id")
        monkeypatch.setenv("APP_SECRET", "test_secret")
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        from src.config import Config
        cfg = Config.from_env()
        assert cfg.log_level == "INFO"

    def test_from_env_reads_log_level_from_env(self, monkeypatch):
        """Config.from_env() reads LOG_LEVEL from environment when set."""
        monkeypatch.setenv("APP_ID", "test_id")
        monkeypatch.setenv("APP_SECRET", "test_secret")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        from src.config import Config
        cfg = Config.from_env()
        assert cfg.log_level == "DEBUG"

    def test_from_env_defaults_working_dir_to_dot(self, monkeypatch):
        """Config.from_env() uses WORKING_DIR='.' when not set."""
        monkeypatch.setenv("APP_ID", "test_id")
        monkeypatch.setenv("APP_SECRET", "test_secret")
        monkeypatch.delenv("WORKING_DIR", raising=False)

        from src.config import Config
        cfg = Config.from_env()
        assert cfg.working_dir == "."
