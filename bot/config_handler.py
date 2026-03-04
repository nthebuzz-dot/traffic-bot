import copy
import os

import yaml

DEFAULTS = {
    'sessions_count': 10,
    'concurrent_sessions': 1,
    'session_duration': 45,
    'duration_seconds': 600,
    'proxies': [],
    'headless': True,
    'chromium_path': None,
}


class ConfigHandler:
    """Load, validate and expose bot configuration."""

    def __init__(self, config_file=None):
        self.config_file = config_file
        self.config = copy.deepcopy(DEFAULTS)
        if config_file:
            self._load_config(config_file)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_config(self, config_file):
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file not found: {config_file}")
        with open(config_file, 'r') as fh:
            data = yaml.safe_load(fh) or {}
        self.config.update(data)

    def validate(self):
        """Raise ValueError if the config is not usable."""
        if not self.config.get('target_url'):
            raise ValueError(
                "target_url is required. Pass --url on the command line or set it in the config file."
            )

    # ------------------------------------------------------------------
    # Attribute-style access (used by TrafficBot and CLI)
    # ------------------------------------------------------------------

    @property
    def target_url(self):
        return self.config.get('target_url')

    @target_url.setter
    def target_url(self, value):
        self.config['target_url'] = value

    @property
    def sessions_count(self):
        return int(self.config.get('sessions_count', DEFAULTS['sessions_count']))

    @sessions_count.setter
    def sessions_count(self, value):
        self.config['sessions_count'] = int(value)

    @property
    def concurrent_sessions(self):
        # Minimum 1; no upper cap — user is responsible for their server's RAM
        return max(1, int(self.config.get('concurrent_sessions', DEFAULTS['concurrent_sessions'])))

    @concurrent_sessions.setter
    def concurrent_sessions(self, value):
        self.config['concurrent_sessions'] = max(1, int(value))

    @property
    def session_duration(self):
        return int(self.config.get('session_duration', DEFAULTS['session_duration']))

    @session_duration.setter
    def session_duration(self, value):
        self.config['session_duration'] = int(value)

    @property
    def duration_seconds(self):
        return int(self.config.get('duration_seconds', DEFAULTS['duration_seconds']))

    @duration_seconds.setter
    def duration_seconds(self, value):
        self.config['duration_seconds'] = int(value)

    @property
    def proxies(self):
        return self.config.get('proxies') or []

    @proxies.setter
    def proxies(self, value):
        self.config['proxies'] = value or []

    @property
    def headless(self):
        return bool(self.config.get('headless', DEFAULTS['headless']))

    @headless.setter
    def headless(self, value):
        self.config['headless'] = bool(value)

    @property
    def chromium_path(self):
        path = self.config.get('chromium_path')
        return path if path else None

    @chromium_path.setter
    def chromium_path(self, value):
        self.config['chromium_path'] = value

    # ------------------------------------------------------------------
    # Dict-style access (kept for backwards compatibility)
    # ------------------------------------------------------------------

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        if self.config_file:
            self._save_config()

    def _save_config(self):
        with open(self.config_file, 'w') as fh:
            yaml.safe_dump(self.config, fh)
