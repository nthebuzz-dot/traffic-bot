import copy
import os

import yaml

DEFAULTS = {
    'target_url': '',
    'target_urls': [],          # list of URLs; takes priority over target_url
                                # Each entry may be "url | ref1, ref2" for per-URL referrers
    'sessions_count': 10,
    'concurrent_sessions': 1,
    'session_duration': 45,
    'duration_seconds': 600,
    'proxies': [],
    'headless': True,
    'chromium_path': None,
    'cookie_dir': None,         # directory for persistent cookie storage (None = default)
    'referrers': [],            # global custom referrer URLs; used for ~80% of sessions
                                # Per-URL referrers override this when set via "url | ref" format
}


def _parse_url_line(line: str):
    """
    Parse a URL line that may contain per-URL referrers.

    Supported formats:
      "https://target.com"
        → url="https://target.com", referrers=[]

      "https://target.com | https://ref1.com, https://ref2.com"
        → url="https://target.com", referrers=["https://ref1.com", "https://ref2.com"]

    Returns:
        (url_str, referrer_list)  where referrer_list may be empty.
    """
    if '|' in line:
        parts = line.split('|', 1)
        url = parts[0].strip()
        refs_raw = parts[1].strip()
        refs = [r.strip() for r in refs_raw.replace(',', '\n').splitlines() if r.strip()]
        return url, refs
    return line.strip(), []


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
        Return the canonical list of target URLs (without referrer suffixes).

        Priority:
          1. target_urls list (if non-empty) — each entry may be "url | ref1, ref2"
          2. target_url single string (backwards-compat)
        """
        raw = [u.strip() for u in (self.config.get('target_urls') or []) if u and u.strip()]
        if raw:
            return [_parse_url_line(u)[0] for u in raw if _parse_url_line(u)[0]]
        single = (self.config.get('target_url') or '').strip()
        if single:
            return [_parse_url_line(single)[0]]
        return []

    @property
    def url_referrers(self) -> dict:
        """
        Return a dict mapping each target URL to its per-URL referrer list.

        When a URL line contains "url | ref1, ref2", those referrers are
        used for that URL instead of the global referrers list.

        Returns:
            {url: [referrer, ...]}  — empty list means "use global referrers"
        """
        raw = [u.strip() for u in (self.config.get('target_urls') or []) if u and u.strip()]
        result = {}
        for entry in raw:
            url, refs = _parse_url_line(entry)
            if url:
                result[url] = refs
        # Also handle the legacy single target_url
        single = (self.config.get('target_url') or '').strip()
        if single and single not in result:
            url, refs = _parse_url_line(single)
            if url:
                result[url] = refs
        return result

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
