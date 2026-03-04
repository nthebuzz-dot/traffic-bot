"""
Cookie persistence manager for Web Traffic Bot.

Saves and loads browser cookies per domain so that each session looks like
a returning visitor rather than a fresh one.  This is one of the strongest
signals that bot-detection systems use: a real user almost always has cookies
from previous visits, while a bot typically starts with an empty jar.

Design
------
- Cookies are stored as JSON files in a configurable directory
  (default: ~/.web-traffic-bot/cookies/).
- One file per domain: ``<cookie_dir>/<sanitised_domain>.json``
- After a session completes, the driver's cookies are merged into the
  on-disk jar (new cookies are added; existing ones are updated).
- On the next session for the same domain, the saved cookies are injected
  into the browser *after* the initial page load (Selenium requires the
  browser to be on the target domain before cookies can be set).
- A small set of realistic "consent" cookies is also injected to suppress
  GDPR/cookie-banner popups that would otherwise appear on every fresh visit.

Thread safety
-------------
A per-domain file lock (threading.Lock) prevents concurrent sessions from
corrupting the same cookie file when multiple sessions run in parallel.
"""

import json
import os
import re
import threading
from typing import Dict, List, Optional
from urllib.parse import urlparse

from bot.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Default cookie storage location
# ---------------------------------------------------------------------------

_DEFAULT_COOKIE_DIR = os.path.join(
    os.path.expanduser("~"), ".web-traffic-bot", "cookies"
)

# ---------------------------------------------------------------------------
# Realistic "consent" cookies injected on every visit.
#
# Many sites set these on first visit via a consent banner.  Injecting them
# upfront means the banner never appears, which looks more natural and avoids
# the bot getting stuck on a consent overlay.
#
# These are generic / widely-used cookie names.  They do NOT contain any
# real tracking data — they just signal "user has already accepted cookies".
# ---------------------------------------------------------------------------

_CONSENT_COOKIES: List[Dict] = [
    # Google Consent Mode v2
    {"name": "CONSENT",        "value": "YES+cb.20240101-00-p0.en+FX+001", "domain": ".google.com"},
    # Generic GDPR consent (used by OneTrust, Cookiebot, etc.)
    {"name": "cookieconsent_status",  "value": "dismiss"},
    {"name": "cookie_consent",        "value": "1"},
    {"name": "gdpr_consent",          "value": "1"},
    {"name": "CookieConsent",         "value": "{stamp:%27accepted%27,necessary:true,preferences:true,statistics:true,marketing:true}"},
    # OneTrust
    {"name": "OptanonAlertBoxClosed", "value": "2024-01-01T00:00:00.000Z"},
    {"name": "OptanonConsent",        "value": "isGpcEnabled=0&datestamp=Mon+Jan+01+2024&version=202401.1.0&isIABGlobal=false&hosts=&consentId=abc123&interactionCount=1&landingPath=NotLandingPage&groups=C0001%3A1%2CC0002%3A1%2CC0003%3A1%2CC0004%3A1"},
    # Cookiebot
    {"name": "CookieConsentV2",       "value": "{%22stamp%22:%22accepted%22,%22necessary%22:true,%22preferences%22:true,%22statistics%22:true,%22marketing%22:true,%22method%22:%22explicit%22,%22ver%22:1,%22utc%22:1704067200,%22region%22:%22gb%22}"},
    # Cloudflare bot challenge bypass hint (not a real bypass — just the preference cookie)
    {"name": "__cf_bm",               "value": ""},   # value intentionally blank; presence matters
]

# ---------------------------------------------------------------------------
# Per-domain file locks (prevents concurrent sessions corrupting the same file)
# ---------------------------------------------------------------------------

_domain_locks: Dict[str, threading.Lock] = {}
_locks_meta_lock = threading.Lock()


def _get_domain_lock(domain: str) -> threading.Lock:
    with _locks_meta_lock:
        if domain not in _domain_locks:
            _domain_locks[domain] = threading.Lock()
        return _domain_locks[domain]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitise_domain(domain: str) -> str:
    """Convert a domain string to a safe filename component."""
    # Strip leading dot (e.g. '.example.com' → 'example.com')
    domain = domain.lstrip(".")
    # Replace any character that isn't alphanumeric, dot, or hyphen
    return re.sub(r"[^a-zA-Z0-9.\-]", "_", domain)


def _domain_from_url(url: str) -> str:
    """Extract the bare domain from a URL (e.g. 'https://www.example.com/path' → 'example.com')."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    # Strip 'www.' prefix so cookies are shared across www and non-www
    if host.startswith("www."):
        host = host[4:]
    return host


def _cookie_file(domain: str, cookie_dir: str) -> str:
    return os.path.join(cookie_dir, f"{_sanitise_domain(domain)}.json")


def _load_cookies(domain: str, cookie_dir: str) -> List[Dict]:
    """Load saved cookies for *domain* from disk.  Returns [] on any error."""
    path = _cookie_file(domain, cookie_dir)
    try:
        if os.path.exists(path):
            with open(path, "r") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                logger.debug(f"Loaded {len(data)} saved cookies for {domain}")
                return data
    except Exception as e:
        logger.debug(f"Could not load cookies for {domain}: {e}")
    return []


def _save_cookies(domain: str, cookies: List[Dict], cookie_dir: str) -> None:
    """Merge *cookies* into the on-disk jar for *domain*."""
    lock = _get_domain_lock(domain)
    with lock:
        existing = _load_cookies(domain, cookie_dir)
        # Build a dict keyed by (name, domain) for deduplication
        jar: Dict[tuple, Dict] = {
            (c.get("name", ""), c.get("domain", "")): c
            for c in existing
        }
        for c in cookies:
            key = (c.get("name", ""), c.get("domain", ""))
            jar[key] = c

        path = _cookie_file(domain, cookie_dir)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump(list(jar.values()), fh, indent=2)
            logger.debug(f"Saved {len(jar)} cookies for {domain}")
        except Exception as e:
            logger.debug(f"Could not save cookies for {domain}: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class CookieManager:
    """
    Manages cookie persistence and injection for a single browser session.

    Usage::

        mgr = CookieManager(driver, target_url, cookie_dir="/path/to/cookies")
        mgr.inject()          # call AFTER driver.get(url) — loads saved cookies
                              # and injects consent cookies, then refreshes the page
        # ... run session ...
        mgr.save()            # call BEFORE driver.quit() — persists new cookies
    """

    def __init__(self, driver, target_url: str,
                 cookie_dir: Optional[str] = None):
        self.driver = driver
        self.target_url = target_url
        self.domain = _domain_from_url(target_url)
        self.cookie_dir = cookie_dir or _DEFAULT_COOKIE_DIR

    # ------------------------------------------------------------------
    # Inject
    # ------------------------------------------------------------------

    def inject(self) -> None:
        """
        Inject saved + consent cookies into the current browser session.

        Must be called AFTER ``driver.get(url)`` because Selenium requires
        the browser to already be on the target domain before cookies can
        be set for that domain.

        After injection the page is refreshed so the site sees the cookies
        on the very first "real" request (avoids the consent banner, etc.).
        """
        if not self.domain:
            return

        injected = 0

        # 1. Inject consent cookies (domain-agnostic — set for current domain)
        for template in _CONSENT_COOKIES:
            # Skip cookies that are explicitly for a different domain
            # (e.g. the Google CONSENT cookie)
            if "domain" in template and not self.domain.endswith(
                template["domain"].lstrip(".")
            ):
                continue
            cookie = {
                "name":   template["name"],
                "value":  template["value"],
                "path":   "/",
                "secure": False,
                "httpOnly": False,
            }
            try:
                self.driver.add_cookie(cookie)
                injected += 1
            except Exception:
                pass  # some cookies may be rejected — that's fine

        # 2. Inject previously saved cookies for this domain
        saved = _load_cookies(self.domain, self.cookie_dir)
        for cookie in saved:
            # Strip fields that Selenium doesn't accept
            safe = {k: v for k, v in cookie.items()
                    if k in ("name", "value", "path", "secure", "httpOnly",
                             "expiry", "sameSite")}
            if not safe.get("name"):
                continue
            try:
                self.driver.add_cookie(safe)
                injected += 1
            except Exception:
                pass

        if injected:
            logger.debug(f"Injected {injected} cookies for {self.domain}; refreshing page")
            try:
                self.driver.refresh()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(self) -> None:
        """
        Persist the current browser cookies to disk for future sessions.

        Call this BEFORE ``driver.quit()``.
        """
        if not self.domain:
            return
        try:
            cookies = self.driver.get_cookies()
            if cookies:
                _save_cookies(self.domain, cookies, self.cookie_dir)
        except Exception as e:
            logger.debug(f"Could not retrieve cookies from driver: {e}")
