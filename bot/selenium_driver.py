import json
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from bot.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Device fingerprint pools — each session picks randomly from these
# ---------------------------------------------------------------------------

# Realistic desktop user-agents across different OS / browser combinations
USER_AGENTS = [
    # Windows Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # macOS Chrome
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Linux Chrome
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Windows Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    # macOS Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    # macOS Firefox
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Windows Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

# Common desktop screen resolutions
_SCREEN_SIZES = [
    "1920,1080",
    "1366,768",
    "1440,900",
    "1536,864",
    "1280,800",
    "1600,900",
    "2560,1440",
    "1280,1024",
]

# Accept-Language header values
_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-CA,en;q=0.9",
    "en-AU,en;q=0.8,en;q=0.7",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "de-DE,de;q=0.9,en;q=0.8",
    "es-ES,es;q=0.9,en;q=0.8",
]

# Fallback timezones when no proxy is configured or GeoIP lookup fails
_FALLBACK_TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Los_Angeles",
    "Europe/London", "Europe/Paris", "Europe/Berlin",
    "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney",
]

# ---------------------------------------------------------------------------
# Proxy-aware timezone lookup
# ---------------------------------------------------------------------------

# Cache: proxy_host → timezone string (avoids repeated GeoIP calls)
_proxy_tz_cache: dict = {}

# Pre-resolved driver path cache (populated by resolve_driver_once())
_driver_path_cache: Optional[str] = None
_driver_path_lock = threading.Lock()


def _get_proxy_timezone(proxy_url: Optional[str]) -> Optional[str]:
    """
    Determine the timezone of the proxy's **exit IP** by making a GeoIP
    request *through* the proxy itself.

    This gives the correct timezone even when the proxy IP and exit IP
    are in different countries (e.g. residential or rotating proxies).

    Uses the free ip-api.com service (no API key, 45 req/min).
    Results are cached per proxy URL to avoid repeated lookups.

    Returns a timezone string (e.g. 'America/New_York') or None on failure.
    """
    if not proxy_url:
        return None

    if proxy_url in _proxy_tz_cache:
        return _proxy_tz_cache[proxy_url]

    try:
        # Build a proxy handler so the request goes through the proxy
        proxy_handler = urllib.request.ProxyHandler({
            "http":  proxy_url,
            "https": proxy_url,
        })
        opener = urllib.request.build_opener(proxy_handler)

        # Ask ip-api.com what IP it sees — this is the proxy's exit IP
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=timezone,status,query",
            headers={"User-Agent": "web-traffic-bot/1.0"},
        )
        with opener.open(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        if data.get("status") == "success" and data.get("timezone"):
            tz = data["timezone"]
            exit_ip = data.get("query", "?")
            _proxy_tz_cache[proxy_url] = tz
            logger.debug(f"Proxy exit IP {exit_ip} → timezone: {tz}")
            return tz

    except Exception as e:
        logger.debug(f"Proxy timezone lookup failed: {e}")

    return None


# ---------------------------------------------------------------------------
# Known browser / driver binary locations (checked in order)
# ---------------------------------------------------------------------------

# IMPORTANT: Snap Chromium CANNOT be used with Selenium.
# The snap sandbox prevents chromedriver from launching the snap binary
# as a subprocess. Use the apt version instead:
#   sudo snap remove chromium
#   sudo apt install -y chromium-browser chromium-chromedriver   # Ubuntu 20.04
#   sudo apt install -y chromium chromium-driver                 # Ubuntu 22.04+

# (browser_binary, chromedriver_binary) pairs that are known to be compatible.
# NOTE: On Ubuntu 22.04+, /usr/bin/chromium-browser is a STUB that just says
# "install the snap". We must NOT use it. The real apt binary is /usr/bin/chromium.
_KNOWN_PAIRS = [
    # Debian/Ubuntu apt package (22.04+ non-snap) — check BEFORE chromium-browser
    ("/usr/bin/chromium",               "/usr/bin/chromedriver"),
    # Debian/Ubuntu apt package (20.04) — only valid if it's a real binary, not a stub
    ("/usr/bin/chromium-browser",       "/usr/lib/chromium-browser/chromedriver"),
    # Google Chrome (Debian package)
    ("/usr/bin/google-chrome-stable",   None),   # driver resolved separately
    ("/usr/bin/google-chrome",          None),
]

# Snap binary paths — detected to show a helpful error, NOT used for launching
_SNAP_BROWSER_PATHS = [
    "/snap/bin/chromium",
    "/snap/chromium/current/usr/bin/chromium",
]

# Stub script paths — Ubuntu 22.04+ installs these as snap redirectors.
# They are NOT real browsers and must be skipped.
_STUB_PATHS = [
    "/usr/bin/chromium-browser",   # Ubuntu 22.04+ stub → "install snap chromium"
]

# Standalone driver candidates (used when browser is found but driver is None above)
_DRIVER_CANDIDATES = [
    "chromedriver",
    "chromium-chromedriver",
    "chromium.chromedriver",
]

# Standalone browser candidates (fallback)
_BROWSER_CANDIDATES = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
]


def _is_snap_stub(path: str) -> bool:
    """
    Return True if *path* is the Ubuntu 22.04+ snap redirect stub.
    The stub is a shell script that just prints 'install snap chromium'.
    We detect it by reading the first 512 bytes and looking for 'snap'.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(512).decode("utf-8", errors="ignore")
        return "snap" in header.lower() and "install" in header.lower()
    except Exception:
        return False


def _binary_exists(path: str) -> bool:
    """Return True if *path* is a real executable (not a snap stub)."""
    if not (bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)):
        return False
    # Skip Ubuntu 22.04+ snap redirect stubs
    if path in _STUB_PATHS and _is_snap_stub(path):
        logger.debug(f"Skipping snap stub: {path}")
        return False
    return True


def _find_on_path(candidates: list) -> Optional[str]:
    """Return the first candidate found on PATH, or None."""
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def _get_version(binary: str) -> Optional[str]:
    """Return the full version string of a Chrome/Chromium binary, or None."""
    try:
        out = subprocess.check_output(
            [binary, "--version"], stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
        return out
    except Exception:
        return None


def _get_major_version(binary: str) -> Optional[int]:
    """Return the major version integer of a Chrome/Chromium binary, or None."""
    ver = _get_version(binary)
    if not ver:
        return None
    for part in ver.split():
        if part[0].isdigit():
            try:
                return int(part.split(".")[0])
            except ValueError:
                pass
    return None


def _versions_match(browser_bin: str, driver_bin: str) -> bool:
    """Return True if browser and driver have the same major version."""
    bv = _get_major_version(browser_bin)
    dv = _get_major_version(driver_bin)
    if bv is None or dv is None:
        return True  # can't check — assume OK
    if bv != dv:
        logger.warning(
            f"Version mismatch: browser={bv}, chromedriver={dv}. "
            "Will use webdriver-manager to download the correct driver."
        )
        return False
    return True


def _resolve_browser_and_driver(
    explicit_browser: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (browser_binary, chromedriver_binary).

    Strategy (in order):
    1. If the user supplied an explicit browser path, use it and find a
       matching driver.
    2. Walk the known (browser, driver) pairs and return the first where
       both binaries exist.
    3. Search PATH for any browser candidate and any driver candidate
       independently (last resort — may mismatch on snap systems).
    """
    # --- 0. Snap Chromium detection — fail fast with a clear message ---
    for snap_path in _SNAP_BROWSER_PATHS:
        if _binary_exists(snap_path):
            raise RuntimeError(
                "Snap Chromium detected but it CANNOT be used with Selenium.\n"
                "The snap sandbox prevents chromedriver from launching the browser.\n"
                "\n"
                "Fix — replace snap Chromium with the apt version:\n"
                "  sudo snap remove chromium\n"
                "  sudo apt update\n"
                "  sudo apt install -y chromium chromium-driver        # Ubuntu 22.04+\n"
                "  # OR for Ubuntu 20.04:\n"
                "  sudo apt install -y chromium-browser chromium-chromedriver\n"
                "\n"
                "Then restart the dashboard."
            )

    # --- 1. Explicit browser path ---
    if explicit_browser and _binary_exists(explicit_browser):
        driver = _find_on_path(_DRIVER_CANDIDATES)
        logger.info(f"Using explicit browser: {explicit_browser}")
        if driver:
            logger.info(f"ChromeDriver: {driver}")
        return explicit_browser, driver

    # --- 2. Known compatible pairs ---
    for browser, driver in _KNOWN_PAIRS:
        if _binary_exists(browser):
            if driver is None:
                # Browser found but driver not specified — search PATH
                driver = _find_on_path(_DRIVER_CANDIDATES)
            if driver and _binary_exists(driver):
                # Verify versions match before committing to this pair
                if _versions_match(browser, driver):
                    logger.info(f"Browser binary : {browser}")
                    logger.info(f"ChromeDriver   : {driver}")
                    ver = _get_version(browser)
                    if ver:
                        logger.info(f"Browser version: {ver}")
                    return browser, driver
                else:
                    # Version mismatch — skip system driver, use webdriver-manager
                    logger.info(f"Browser binary : {browser}")
                    ver = _get_version(browser)
                    if ver:
                        logger.info(f"Browser version: {ver}")
                    return browser, None
            elif driver is None:
                # Browser found, no driver anywhere — return browser only
                # (will fall back to webdriver-manager)
                logger.info(f"Browser binary : {browser}")
                ver = _get_version(browser)
                if ver:
                    logger.info(f"Browser version: {ver}")
                return browser, None

    # --- 3. PATH search (independent) ---
    browser = _find_on_path(_BROWSER_CANDIDATES)
    driver = _find_on_path(_DRIVER_CANDIDATES)

    if browser:
        logger.info(f"Browser binary : {browser}")
        ver = _get_version(browser)
        if ver:
            logger.info(f"Browser version: {ver}")
    else:
        logger.warning(
            "No Chrome/Chromium binary found. "
            "Install with: sudo apt install -y chromium chromium-driver"
        )

    if driver:
        logger.info(f"ChromeDriver   : {driver}")
    else:
        logger.warning(
            "No chromedriver found. "
            "Install with: sudo apt install -y chromium-driver"
        )

    return browser, driver


def _get_wdm_service(chromium_path: Optional[str] = None) -> Service:
    """
    Download the correct chromedriver and return a Service.

    Strategy (in order):
    1. webdriver-manager with ChromeType.CHROMIUM (for Chromium browsers)
    2. webdriver-manager generic (for Google Chrome)
    3. Selenium Manager fallback — pass Service() with no path and let
       Selenium 4.6+ auto-resolve the driver (handles Chrome 115+)
    """
    browser_bin = chromium_path or _find_on_path(_BROWSER_CANDIDATES)
    is_chromium = bool(browser_bin and "chromium" in browser_bin.lower())

    try:
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        # webdriver-manager not installed — fall through to Selenium Manager
        logger.warning("webdriver-manager not installed; using Selenium Manager")
        logger.info("Selenium Manager will auto-download the correct chromedriver...")
        return Service()  # Selenium 4.6+ Selenium Manager handles this

    if is_chromium:
        try:
            from webdriver_manager.core.os_manager import ChromeType
            logger.info("Downloading chromedriver for Chromium via webdriver-manager...")
            return Service(
                ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
            )
        except Exception as e:
            logger.warning(f"ChromeType.CHROMIUM download failed ({e}), trying generic...")

    try:
        logger.info("Downloading chromedriver via webdriver-manager...")
        return Service(ChromeDriverManager().install())
    except Exception as e:
        # webdriver-manager failed (e.g. Chrome 145 not in its database yet)
        # Fall back to Selenium Manager which handles any Chrome version
        logger.warning(
            f"webdriver-manager failed ({e}). "
            "Falling back to Selenium Manager (auto-downloads correct driver)..."
        )
        return Service()  # Selenium 4.6+ will invoke Selenium Manager automatically


def resolve_driver_once(chromium_path: Optional[str] = None) -> Optional[str]:
    """
    Resolve and cache the chromedriver path exactly **once** (thread-safe).

    Call this BEFORE starting the concurrent session thread pool so that all
    sessions reuse the same pre-downloaded driver path instead of racing to
    download it simultaneously (which causes zip corruption and tuple errors).

    Returns the absolute path to chromedriver, or None on failure.
    """
    global _driver_path_cache

    with _driver_path_lock:
        if _driver_path_cache is not None:
            return _driver_path_cache

        # Try system driver first (no download needed)
        try:
            browser_bin, driver_bin = _resolve_browser_and_driver(chromium_path)
            if driver_bin:
                logger.info(f"Pre-resolved system chromedriver: {driver_bin}")
                _driver_path_cache = driver_bin
                return driver_bin
        except RuntimeError:
            raise  # snap Chromium error — propagate immediately

        # No system driver — download once via webdriver-manager or Selenium Manager
        logger.info(
            "Pre-downloading chromedriver "
            "(once for all concurrent sessions)..."
        )
        try:
            svc = _get_wdm_service(chromium_path)
            path = svc.path if svc.path else None
            if path:
                _driver_path_cache = path
                logger.info(f"ChromeDriver cached at: {path}")
                return path
            else:
                # Selenium Manager fallback — driver will be resolved per-session
                # (Service() with no path triggers Selenium Manager automatically)
                logger.info(
                    "Selenium Manager will resolve chromedriver per-session "
                    "(Chrome 145+ support)"
                )
                _driver_path_cache = ""  # sentinel: "use Service() with no path"
                return None
        except Exception as e:
            logger.error(f"Failed to pre-download chromedriver: {e}")
            return None


class SeleniumDriver:
    """Wrapper for Selenium WebDriver with Chromium/Chrome.

    Handles snap Chromium, apt Chromium, and Google Chrome automatically.
    The snap version of Chromium ships its own chromedriver — this class
    detects and uses the matching pair so versions never mismatch.
    """

    def __init__(self, headless: bool = True, proxy: Optional[str] = None,
                 chromium_path: Optional[str] = None,
                 driver_path: Optional[str] = None):
        """
        Args:
            headless:     Run Chrome without a visible window.
            proxy:        Proxy URL (e.g. 'http://user:pass@host:port').
            chromium_path: Path to the Chrome/Chromium binary (auto-detected if None).
            driver_path:  Pre-resolved chromedriver path from resolve_driver_once().
                          Pass this when running many concurrent sessions to avoid
                          race conditions in webdriver-manager's download cache.
        """
        self.headless = headless
        self.proxy = proxy
        self.chromium_path = chromium_path
        self._driver_path = driver_path  # pre-resolved, skips resolution per session
        self.driver = None
        self._tmp_dir: Optional[str] = None
        self._setup_driver()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _build_options(self) -> Options:
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")
            logger.info("Running in headless mode")

        # ----------------------------------------------------------------
        # Flags required for stable operation on headless servers / Docker
        # ----------------------------------------------------------------
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins")
        # Note: window size is set randomly in the fingerprint section below

        # Fix "DevToolsActivePort file doesn't exist" on servers:
        # Use a dedicated temp directory for each Chrome instance so
        # multiple sessions don't collide, and disable the remote
        # debugging port that causes the crash.
        self._tmp_dir = tempfile.mkdtemp(prefix="chrome_tmp_")
        options.add_argument(f"--user-data-dir={self._tmp_dir}")
        options.add_argument("--remote-debugging-port=0")  # 0 = OS picks a free port

        # Additional stability flags for server environments
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-translate")
        options.add_argument("--metrics-recording-only")
        options.add_argument("--mute-audio")
        options.add_argument("--no-first-run")
        options.add_argument("--safebrowsing-disable-auto-update")
        options.add_argument("--single-process")   # helps in low-memory VPS environments

        # Suppress "Chrome is being controlled by automated software" bar
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # ----------------------------------------------------------------
        # Device fingerprint randomisation
        # Each session gets a different combination of UA, screen size,
        # language, and timezone to look like a different device/user.
        # ----------------------------------------------------------------

        # Random user-agent
        ua = random.choice(USER_AGENTS)
        options.add_argument(f"--user-agent={ua}")
        logger.debug(f"User-agent: {ua}")

        # Random screen resolution (overrides the fixed 1920,1080 above)
        screen = random.choice(_SCREEN_SIZES)
        w, h = screen.split(",")
        options.add_argument(f"--window-size={w},{h}")

        # Random Accept-Language
        lang = random.choice(_LANGUAGES)
        options.add_argument(f"--lang={lang}")

        # Timezone: use the proxy's geographic timezone if available,
        # otherwise fall back to a random one from the module-level pool.
        tz = _get_proxy_timezone(self.proxy) or random.choice(_FALLBACK_TIMEZONES)
        options.add_argument(f"--timezone={tz}")
        logger.debug(f"Timezone: {tz}")

        if self.proxy:
            logger.info(f"Setting proxy: {self.proxy}")
            options.add_argument(f"--proxy-server={self.proxy}")

        return options

    def _setup_driver(self):
        options = self._build_options()

        # If a pre-resolved driver path was supplied (from resolve_driver_once()),
        # use it directly — skip the per-session resolution to avoid race conditions.
        if self._driver_path:
            browser_bin, _ = _resolve_browser_and_driver(self.chromium_path)
            if browser_bin:
                options.binary_location = browser_bin
            try:
                service = Service(self._driver_path)
                self.driver = webdriver.Chrome(service=service, options=options)
                logger.info("Selenium WebDriver initialised successfully (pre-resolved driver)")
                return
            except Exception:
                pass  # fall through to normal resolution

        browser_bin, driver_bin = _resolve_browser_and_driver(self.chromium_path)

        # Tell Selenium which browser binary to use
        if browser_bin:
            options.binary_location = browser_bin

        try:
            if driver_bin:
                service = Service(driver_bin)
            else:
                # No matching system chromedriver — use webdriver-manager to download
                # the correct version for the installed browser.
                logger.info(
                    "No matching system chromedriver found — using webdriver-manager "
                    "to download the correct version. This requires internet access."
                )
                service = self._get_webdriver_manager_service()

            self.driver = webdriver.Chrome(service=service, options=options)
            logger.info("Selenium WebDriver initialised successfully")

        except Exception as e:
            # Clean up the temp dir if Chrome failed to start
            if self._tmp_dir:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
                self._tmp_dir = None
            logger.error(
                f"WebDriver initialisation failed: {e}\n"
                "Troubleshooting:\n"
                "  1. Check browser version:   chromium-browser --version\n"
                "  2. Check driver version:    chromedriver --version\n"
                "  3. If versions mismatch, the bot will auto-download the correct driver\n"
                "     via webdriver-manager (requires internet access).\n"
                "  4. Or set 'Chromium Path' in the dashboard to your browser binary path."
            )
            raise

    def _get_webdriver_manager_service(self) -> Service:
        """Download and return a Service using webdriver-manager."""
        return _get_wdm_service(self.chromium_path)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def get(self, url: str):
        try:
            logger.debug(f"Navigating to: {url}")
            self.driver.get(url)
            time.sleep(random.uniform(1.5, 3.0))
        except Exception as e:
            logger.error(f"Failed to navigate to {url}: {e}")
            raise

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def quit(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
            logger.debug("WebDriver closed")
        # Clean up the temporary user-data-dir to avoid disk accumulation
        if self._tmp_dir:
            try:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
            except Exception:
                pass
            self._tmp_dir = None
