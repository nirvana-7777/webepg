# src/config.py
"""
Configuration management for EPG service.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class Config:
    """Configuration manager with YAML and environment variable support."""

    DEFAULT_CONFIG = {
        "database": {"path": "epg.db"},
        "server": {
            "host": "0.0.0.0",
            "port": 8080,
            "debug": False,
            "cors_enabled": False,
        },
        "retention": {"days": 7},
        "scheduler": {"import_time": "03:00", "timezone": "UTC"},
        "logging": {"level": "INFO", "format": "text"},
        "ultimate_backend": {
            "enabled": False,
            "instance": {
                "name": "main",
                "base_url": "http://ultimate:7777",
                "api_key": None,
            },
            "import": {
                "future_days": 7,
                "past_days": 7,
                "chunk_hours": 24,
                "max_requests_per_second": 5,
                "max_concurrent_channels": 3,
                "timeout_seconds": 30,
                "max_retries": 3,
                "api_max_future_days": 3,
            },
            "discovery": {
                "enabled": True,
                "day": 6,  # Sunday
                "hour": 2,
            },
            "retry": {
                "max_attempts": 3,
                "base_delay_seconds": 1,
                "max_delay_seconds": 30,
            },
            "grid_import": {
                "chunk_hours": 3,
                "days_ahead": 7,
            },
            "detail_enrichment": {
                "enabled": True,
                "max_days": 7,
                "max_attempts": 3,
            },
        },
    }

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration.

        Args:
            config_path: Path to YAML config file (optional)
        """
        self.config = self.DEFAULT_CONFIG.copy()

        # Load from YAML file if provided
        if config_path and Path(config_path).exists():
            self._load_yaml(config_path)

        # Override with environment variables
        self._load_env_vars()

    def _load_yaml(self, path: str):
        """Load configuration from YAML file."""
        try:
            with open(path, "r") as f:
                yaml_config = yaml.safe_load(f)
                if yaml_config:
                    self._merge_config(self.config, yaml_config)
        except Exception as e:
            print(f"Warning: Failed to load config from {path}: {e}")

    def _merge_config(self, base: Dict, override: Dict):
        """Recursively merge override config into base config."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value

    def _load_env_vars(self):
        """Load configuration from environment variables."""
        # Database
        if "EPG_DB_PATH" in os.environ:
            self.config["database"]["path"] = os.environ["EPG_DB_PATH"]

        # Server
        if "EPG_SERVER_HOST" in os.environ:
            self.config["server"]["host"] = os.environ["EPG_SERVER_HOST"]

        if "EPG_SERVER_PORT" in os.environ:
            self.config["server"]["port"] = int(os.environ["EPG_SERVER_PORT"])

        if "EPG_SERVER_DEBUG" in os.environ:
            self.config["server"]["debug"] = (
                os.environ["EPG_SERVER_DEBUG"].lower() == "true"
            )

        if "EPG_CORS_ENABLED" in os.environ:
            self.config["server"]["cors_enabled"] = (
                os.environ["EPG_CORS_ENABLED"].lower() == "true"
            )

        # Retention
        if "EPG_RETENTION_DAYS" in os.environ:
            self.config["retention"]["days"] = int(os.environ["EPG_RETENTION_DAYS"])

        # Scheduler
        if "EPG_IMPORT_TIME" in os.environ:
            self.config["scheduler"]["import_time"] = os.environ["EPG_IMPORT_TIME"]

        if "EPG_TIMEZONE" in os.environ:
            self.config["scheduler"]["timezone"] = os.environ["EPG_TIMEZONE"]

        # Logging
        if "EPG_LOG_LEVEL" in os.environ:
            self.config["logging"]["level"] = os.environ["EPG_LOG_LEVEL"].upper()

        if "EPG_LOG_FORMAT" in os.environ:
            self.config["logging"]["format"] = os.environ["EPG_LOG_FORMAT"].lower()

        # Ultimate Backend
        if "ULTIMATE_BACKEND_ENABLED" in os.environ:
            self.config["ultimate_backend"]["enabled"] = (
                os.environ["ULTIMATE_BACKEND_ENABLED"].lower() == "true"
            )

        if "ULTIMATE_BACKEND_URL" in os.environ:
            self.config["ultimate_backend"]["instance"]["base_url"] = os.environ[
                "ULTIMATE_BACKEND_URL"
            ]

        if "ULTIMATE_BACKEND_API_KEY" in os.environ:
            self.config["ultimate_backend"]["instance"]["api_key"] = os.environ[
                "ULTIMATE_BACKEND_API_KEY"
            ]

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-notation path.

        Args:
            key_path: Configuration key path (e.g., 'database.path')
            default: Default value if key not found

        Returns:
            Configuration value
        """
        keys = key_path.split(".")
        value = self.config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def get_section(self, section: str) -> Dict:
        """
        Get entire configuration section.

        Args:
            section: Section name (e.g., 'database')

        Returns:
            Configuration section dictionary
        """
        return self.config.get(section, {}).copy()

    def to_dict(self) -> Dict:
        """Get complete configuration as dictionary."""
        return self.config.copy()


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from file and environment.

    Args:
        config_path: Path to YAML config file (optional)

    Returns:
        Config instance
    """
    return Config(config_path)
