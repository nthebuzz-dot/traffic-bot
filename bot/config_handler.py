import copy
import os

import yaml

DEFAULTS = {
    'target_url': '',
    'target_urls': [],          # list of URLs; takes priority over target_url
    'sessions_count': 10,
    'concurrent_sessions': 1,
    'session_duration': 45,
    'duration_seconds': 600,
    'proxies': [],
    'headless': True,
    'chromium_path': None,
    'cookie_dir': None,         # directory for persistent cookie storage (None = default)
    'referrers': [],            # custom referrer URLs; used for ~80% of sessions
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
        if not self.effective_urls:
            raise ValueError(
                "At least one URL is required. Pass --url on the command line "
                "or set target_url / target_urls in the config file."
            )

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------

    @property
    def effective_urls(self) -> list:
        """
        Return the canonical list of target URLs.

        Priority:
          1. target_urls list (if non-empty)
          2. target_url single string (backwards-compat)
        """
        urls = [u.strip() for u in (self.config.get('target_urls') or []) if u and u.strip()]
        if urls:
            return urls
        single = (self.config.get('target_url') or '').strip()
        return [single] if single else []

    # ------------------------------------------------------------------
    # Attribute-style access (used by TrafficBot and CLI)
    # ------------------------------------------------------------------

    @property
    def target_url(self):
        """Return the first URL for backwards compatibility."""
        urls = self.effective_urls
        return urls[0] if urls else ''

    @target_url.setter
    def target_url(self, value):
        self.config['target_url'] = value

    @property
    def target_urls(self):
        return self.config.get('target_urls') or []

    @target_urls.setter
    def target_urls(self, value):
        self.config['target_urls'] = list(value) if value else []

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

    @property
    def cookie_dir(self):
        path = self.config.get('cookie_dir')
        return path if path else None

    @cookie_dir.setter
    def cookie_dir(self, value):
        self.config['cookie_dir'] = value if value else None

    @property
    def referrers(self):
        return self.config.get('referrers') or []

    @referrers.setter
    def referrers(self, value):
        self.config['referrers'] = list(value) if value else []

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
