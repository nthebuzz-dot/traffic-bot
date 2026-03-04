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
# NOTE: All Chrome/Edge UAs use the latest stable release (Chrome 145 / Edge 145).
# Firefox and Safari UAs are also kept current.
# When Chrome releases a new major version, add it here and retire the oldest entry.
USER_AGENTS = [
    # Windows — Chrome 145 (latest stable as of early 2026)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # macOS — Chrome 145
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_3_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # Linux — Chrome 145
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # Windows — Edge 145 (Chromium-based; UA must match Chrome version)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
    # Windows — Firefox 136
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    # macOS — Firefox 136
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15.3; rv:136.0) Gecko/20100101 Firefox/136.0",
    # macOS — Safari 18.x
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
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

# Device pixel ratios (common on real displays)
_DEVICE_PIXEL_RATIOS = [1.0, 1.25, 1.5, 2.0, 2.25, 2.5]

# Realistic referrers — organic search, social, direct
# Used to make sessions look like they came from a real traffic source
# rather than all arriving with no referrer (a strong bot signal at scale).
_REFERRERS = [
    # Google organic search (most common real-world referrer)
    "https://www.google.com/",
    "https://www.google.com/search?q=",
    "https://www.google.co.uk/search?q=",
    "https://www.google.com.au/search?q=",
    "https://www.google.ca/search?q=",
    "https://www.google.de/search?q=",
    "https://www.google.fr/search?q=",
    # Bing
    "https://www.bing.com/search?q=",
    # DuckDuckGo
    "https://duckduckgo.com/?q=",
    # Social
    "https://www.facebook.com/",
    "https://t.co/",
    "https://www.reddit.com/",
    "https://www.linkedin.com/",
    # Direct (no referrer) — ~20% of real traffic
    "",
    "",
    "",
    "",
]

# ---------------------------------------------------------------------------
# JavaScript stealth patches
#
# These are injected via CDP Page.addScriptToEvaluateOnNewDocument so they
# run BEFORE any page script can read the fingerprint properties.
#
# Patches applied:
#   1. navigator.webdriver → undefined  (primary Selenium detection flag)
#   2. navigator.plugins   → realistic plugin list (empty = headless bot)
#   3. navigator.mimeTypes → matching mime types for the fake plugins
#   4. window.chrome       → realistic chrome runtime object
#   5. navigator.permissions.query → spoof 'notifications' as 'default'
#      (headless Chrome returns 'denied' which is a known bot signal)
#   6. Canvas fingerprint noise — tiny per-session pixel perturbation
#      so every session has a unique canvas hash
#   7. WebGL renderer/vendor — spoof to a real GPU string
#   8. navigator.hardwareConcurrency — random realistic value
#   9. navigator.deviceMemory — random realistic value
# ---------------------------------------------------------------------------

_STEALTH_JS = """
(function () {
  'use strict';

  /* 1. Remove navigator.webdriver */
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });

  /* 2 & 3. Realistic plugins + mimeTypes */
  const _plugins = [
    { name: 'Chrome PDF Plugin',        filename: 'internal-pdf-viewer',  description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',        filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',            filename: 'internal-nacl-plugin',  description: '' },
  ];
  const pluginArray = Object.create(PluginArray.prototype);
  _plugins.forEach((p, i) => {
    const plugin = Object.create(Plugin.prototype);
    Object.defineProperty(plugin, 'name',        { get: () => p.name });
    Object.defineProperty(plugin, 'filename',    { get: () => p.filename });
    Object.defineProperty(plugin, 'description', { get: () => p.description });
    Object.defineProperty(plugin, 'length',      { get: () => 0 });
    Object.defineProperty(pluginArray, i,         { get: () => plugin });
  });
  Object.defineProperty(pluginArray, 'length', { get: () => _plugins.length });
  Object.defineProperty(navigator, 'plugins', { get: () => pluginArray });

  /* 4. window.chrome runtime object */
  if (!window.chrome) {
    window.chrome = {
      app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
      runtime: {
        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', GC: 'gc', OS_UPDATE: 'os_update' },
        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
        RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
      },
    };
  }

  /* 5. Permissions — spoof 'notifications' query to return 'default' */
  const _origQuery = window.Notification
    ? Notification.requestPermission
    : null;
  if (navigator.permissions && navigator.permissions.query) {
    const _origPermQuery = navigator.permissions.query.bind(navigator.permissions);
    Object.defineProperty(navigator.permissions, 'query', {
      value: (params) => {
        if (params && params.name === 'notifications') {
          return Promise.resolve({ state: 'default', onchange: null });
        }
        return _origPermQuery(params);
      },
    });
  }

  /* 6. Canvas fingerprint noise — unique per session */
  const _noise = Math.random() * 0.0001;
  const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function (type) {
    const ctx = this.getContext('2d');
    if (ctx) {
      const imageData = ctx.getImageData(0, 0, this.width || 1, this.height || 1);
      imageData.data[0] = imageData.data[0] ^ Math.floor(_noise * 255);
      ctx.putImageData(imageData, 0, 0);
    }
    return _origToDataURL.apply(this, arguments);
  };
  const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function (x, y, w, h) {
    const data = _origGetImageData.apply(this, arguments);
    data.data[0] = data.data[0] ^ Math.floor(_noise * 255);
    return data;
  };

  /* 7. WebGL renderer / vendor spoofing */
  const _getParam = WebGLRenderingContext.prototype.getParameter;
  const _gpuVendors = ['Intel Inc.', 'NVIDIA Corporation', 'AMD', 'Apple Inc.'];
  const _gpuRenderers = [
    'Intel Iris OpenGL Engine',
    'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    'ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    'Apple M1',
  ];
  const _vendor   = _gpuVendors[Math.floor(Math.random() * _gpuVendors.length)];
  const _renderer = _gpuRenderers[Math.floor(Math.random() * _gpuRenderers.length)];
  WebGLRenderingContext.prototype.getParameter = function (param) {
    if (param === 37445) return _vendor;    // UNMASKED_VENDOR_WEBGL
    if (param === 37446) return _renderer;  // UNMASKED_RENDERER_WEBGL
    return _getParam.apply(this, arguments);
  };
  if (typeof WebGL2RenderingContext !== 'undefined') {
    const _getParam2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function (param) {
      if (param === 37445) return _vendor;
      if (param === 37446) return _renderer;
      return _getParam2.apply(this, arguments);
    };
  }

  /* 8. hardwareConcurrency — random 2/4/6/8/12/16 */
  const _cores = [2, 4, 4, 6, 8, 8, 12, 16][Math.floor(Math.random() * 8)];
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => _cores });

  /* 9. deviceMemory — random 2/4/8 GB */
  const _mem = [2, 4, 4, 8, 8][Math.floor(Math.random() * 5)];
  try {
    Object.defineProperty(navigator, 'deviceMemory', { get: () => _mem });
  } catch (_) {}

})();
"""

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
                 driver_path: Optional[str] = None,
                 custom_referrers: Optional[list] = None):
        """
        Args:
            headless:         Run Chrome without a visible window.
            proxy:            Proxy URL (e.g. 'http://user:pass@host:port').
            chromium_path:    Path to the Chrome/Chromium binary (auto-detected if None).
            driver_path:      Pre-resolved chromedriver path from resolve_driver_once().
                              Pass this when running many concurrent sessions to avoid
                              race conditions in webdriver-manager's download cache.
            custom_referrers: List of your own referrer URLs.  When provided, ~80% of
                              sessions will use one of these as the HTTP Referer header
                              so traffic appears to come from your own sites.  The
                              remaining ~20% use the built-in search/social referrers.
                              Pass an empty list or None to use only built-in referrers.
        """
        self.headless = headless
        self.proxy = proxy
        self.chromium_path = chromium_path
        self._driver_path = driver_path  # pre-resolved, skips resolution per session
        self._custom_referrers: list = [r for r in (custom_referrers or []) if r and r.strip()]
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
        # Anti-bot-detection: mask the headless/automation fingerprint.
        #
        # --disable-blink-features=AutomationControlled removes the
        # navigator.webdriver=true flag that Cloudflare, Akamai, DataDome
        # and similar systems check first.
        # ----------------------------------------------------------------
        options.add_argument("--disable-blink-features=AutomationControlled")

        # ----------------------------------------------------------------
        # Flags required for stable operation on headless servers / Docker
        # ----------------------------------------------------------------
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        # NOTE: do NOT add --disable-plugins — it makes the browser look like
        # a bot by removing all plugins (real browsers always have at least
        # the PDF viewer plugin).
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

        # Random screen resolution
        screen = random.choice(_SCREEN_SIZES)
        w, h = screen.split(",")
        options.add_argument(f"--window-size={w},{h}")

        # Random device pixel ratio (1x, 1.5x, 2x, etc.)
        dpr = random.choice(_DEVICE_PIXEL_RATIOS)
        options.add_argument(f"--force-device-scale-factor={dpr}")
        logger.debug(f"Device pixel ratio: {dpr}")

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
                self._inject_stealth_js()
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
            self._inject_stealth_js()
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

    def _inject_stealth_js(self) -> None:
        """
        Inject JavaScript fingerprint patches via CDP so they run before
        any page script can read the fingerprint properties.

        Uses Page.addScriptToEvaluateOnNewDocument which fires on every
        navigation, including iframes — so the patches persist for the
        entire session.
        """
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": _STEALTH_JS},
            )
            logger.debug("Stealth JS patches injected via CDP")
        except Exception as e:
            # CDP may not be available in all environments — log and continue
            logger.debug(f"Stealth JS injection skipped: {e}")

    def _get_webdriver_manager_service(self) -> Service:
        """Download and return a Service using webdriver-manager."""
        return _get_wdm_service(self.chromium_path)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _pick_referrer(self) -> str:
        """
        Pick a referrer URL for this session.

        Priority / weighting:
          - If custom referrers are configured:
              80% → random custom referrer (user's own sites)
              20% → random built-in search/social referrer
          - If no custom referrers:
              100% → random built-in referrer (includes ~20% direct/empty)

        Returns a referrer URL string, or '' for a direct (no-referrer) visit.
        """
        if self._custom_referrers:
            if random.random() < 0.80:
                return random.choice(self._custom_referrers)
            else:
                return random.choice(_REFERRERS)
        return random.choice(_REFERRERS)

    def get(self, url: str):
        """
        Navigate to *url* with a realistic referrer.

        When custom referrers are configured, ~80% of sessions will use one
        of those as the HTTP Referer header so traffic appears to come from
        your own sites.  The remaining ~20% use built-in search/social
        referrers.  When no custom referrers are set, the built-in pool is
        used (which includes ~20% direct/no-referrer visits).
        """
        try:
            logger.debug(f"Navigating to: {url}")

            referrer = self._pick_referrer()
            if referrer:
                # Use CDP Page.navigate with the referrer header so the target
                # site sees it in the HTTP Referer header.
                try:
                    self.driver.execute_cdp_cmd(
                        "Page.navigate",
                        {"url": url, "referrer": referrer},
                    )
                    logger.debug(f"Referrer: {referrer}")
                except Exception:
                    # CDP navigate failed — fall back to plain get()
                    self.driver.get(url)
            else:
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
