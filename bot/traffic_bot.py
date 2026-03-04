import itertools
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

from bot.cookie_manager import CookieManager
from bot.logger import setup_logger
from bot.proxy_manager import ProxyManager
from bot.selenium_driver import SeleniumDriver, resolve_driver_once, cleanup_stale_chrome_tmpdirs
from bot.session_simulator import SessionSimulator

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level stop flag.
# The dashboard sets this to True to request a graceful shutdown.
# TrafficBot.run() resets it to False at the start of each run.
# ---------------------------------------------------------------------------
_STOP_REQUESTED: bool = False

# Thread-safe counters lock
_counter_lock = threading.Lock()


def request_stop() -> None:
    """Ask the currently-running bot to stop after the current session."""
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


class TrafficBot:
    """
    Main traffic bot orchestrator.

    Supports concurrent sessions: up to `concurrent_sessions` browser
    windows run simultaneously. As each one finishes, a new one starts
    immediately so the concurrency level is always maintained until all
    `sessions_count` sessions have been dispatched.

    When multiple URLs are configured, sessions are distributed across
    them in round-robin order and per-URL performance stats are tracked.
    """

    def __init__(self, config):
        self.config = config
        self.proxy_manager = ProxyManager(config.proxies)

        # Global counters
        self.sessions_completed = 0
        self.sessions_failed = 0

        # Per-URL counters  {url: {"completed": int, "failed": int}}
        self._url_stats: Dict[str, Dict[str, int]] = {}
        for url in config.effective_urls:
            self._url_stats[url] = {"completed": 0, "failed": 0}

        # Per-URL referrer map — populated from "url | ref1, ref2" format
        # Falls back to global config.referrers when a URL has no specific referrers
        self._url_referrers: Dict[str, list] = config.url_referrers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def url_stats(self) -> Dict[str, Dict[str, int]]:
        """Return a snapshot of per-URL stats (thread-safe copy)."""
        with _counter_lock:
            return {url: dict(counts) for url, counts in self._url_stats.items()}

    def run(self):
        global _STOP_REQUESTED
        _STOP_REQUESTED = False  # reset on each run

        # Clean up any Chrome temp dirs left over from a previous crash
        # before starting new sessions (prevents disk accumulation).
        cleanup_stale_chrome_tmpdirs()

        concurrency = max(1, self.config.concurrent_sessions)
        urls = self.config.effective_urls

        if not urls:
            raise ValueError(
                "No target URLs configured. "
                "Set target_url or target_urls in the config or dashboard."
            )

        logger.info("=" * 60)
        logger.info("WEB TRAFFIC BOT STARTED")
        logger.info("=" * 60)
        logger.info(f"Target URLs         : {len(urls)}")
        for i, u in enumerate(urls, 1):
            logger.info(f"  [{i}] {u}")
        logger.info(f"Total Sessions      : {self.config.sessions_count}")
        logger.info(f"Concurrent Sessions : {concurrency}")
        logger.info(f"Session Duration    : {self.config.session_duration}s")
        logger.info(f"Total Duration      : {self.config.duration_seconds}s")
        logger.info(f"Proxies             : {len(self.config.proxies)}")
        logger.info(f"Referrers           : {len(self.config.referrers)} custom")
        logger.info(f"Headless            : {self.config.headless}")
        logger.info("=" * 60)

        # Pre-resolve chromedriver ONCE before the thread pool starts.
        # This prevents race conditions when many concurrent sessions all try
        # to download/access the driver cache simultaneously.
        pre_driver_path = resolve_driver_once(self.config.chromium_path)
        if pre_driver_path:
            logger.info(f"ChromeDriver pre-resolved: {pre_driver_path}")

        start_time = time.time()

        # Round-robin URL iterator
        url_cycle = itertools.cycle(urls)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {}
            session_num = 0

            # Fill the pool up to concurrency
            while session_num < self.config.sessions_count:
                # Check hard time limit
                if time.time() - start_time > self.config.duration_seconds:
                    logger.info(f"Total duration reached ({self.config.duration_seconds}s). Stopping.")
                    break

                # Check stop request
                if _STOP_REQUESTED:
                    logger.info("Stop requested. No more sessions will be started.")
                    break

                # Submit up to `concurrency` sessions at once.
                # A small random jitter between session launches (0.5–3s) makes
                # the traffic pattern look organic rather than machine-regular.
                while (len(futures) < concurrency
                       and session_num < self.config.sessions_count
                       and not _STOP_REQUESTED):
                    session_num += 1
                    target_url = next(url_cycle)
                    future = pool.submit(self._run_session, session_num, target_url, pre_driver_path)
                    futures[future] = (session_num, target_url)
                    logger.info(f"[Session {session_num}/{self.config.sessions_count}] started "
                                f"→ {target_url} (active: {len(futures)})")
                    # Inter-session launch jitter — avoids perfectly uniform
                    # session-start timestamps that are a strong bot signal
                    if len(futures) < concurrency and session_num < self.config.sessions_count:
                        jitter = random.uniform(0.5, 3.0)
                        time.sleep(jitter)

                # Wait for at least one to finish before submitting more
                if futures:
                    done_futures = []
                    # Use a short timeout so we can check stop/time limits
                    for f in list(futures.keys()):
                        if f.done():
                            done_futures.append(f)

                    if not done_futures:
                        # Nothing done yet — wait briefly
                        time.sleep(0.5)
                        continue

                    for f in done_futures:
                        snum, url = futures.pop(f)
                        try:
                            f.result()  # re-raise any exception
                            with _counter_lock:
                                self.sessions_completed += 1
                                self._url_stats[url]["completed"] += 1
                            logger.info(f"[Session {snum}] completed ✓  ({url})")
                        except Exception as e:
                            with _counter_lock:
                                self.sessions_failed += 1
                                self._url_stats[url]["failed"] += 1
                            logger.error(f"[Session {snum}] failed: {e}  ({url})")

            # Wait for all remaining in-flight sessions to finish
            logger.info("Waiting for in-flight sessions to finish...")
            for f, (snum, url) in list(futures.items()):
                try:
                    f.result()
                    with _counter_lock:
                        self.sessions_completed += 1
                        self._url_stats[url]["completed"] += 1
                    logger.info(f"[Session {snum}] completed ✓  ({url})")
                except Exception as e:
                    with _counter_lock:
                        self.sessions_failed += 1
                        self._url_stats[url]["failed"] += 1
                    logger.error(f"[Session {snum}] failed: {e}  ({url})")

        self._print_summary(time.time() - start_time)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_session(self, session_num: int, target_url: str,
                     pre_driver_path: Optional[str] = None):
        """Run a single browser session. Called from a thread pool worker."""
        driver = None
        cookie_mgr = None
        try:
            proxy = self.proxy_manager.get_next_proxy()
            if proxy:
                logger.info(f"[Session {session_num}] Using proxy: {proxy}")

            # Use per-URL referrers if configured, otherwise fall back to global referrers
            per_url_refs = self._url_referrers.get(target_url, [])
            effective_referrers = per_url_refs if per_url_refs else (self.config.referrers or [])

            driver = SeleniumDriver(
                headless=self.config.headless,
                proxy=proxy,
                chromium_path=self.config.chromium_path,
                driver_path=pre_driver_path,
                custom_referrers=effective_referrers,
            )
            driver.get(target_url)

            # Inject saved + consent cookies so the session looks like a
            # returning visitor (suppresses consent banners, passes cookie
            # checks used by bot-detection systems).
            cookie_mgr = CookieManager(
                driver.driver, target_url,
                cookie_dir=self.config.cookie_dir or None,
            )
            cookie_mgr.inject()

            simulator = SessionSimulator(driver.driver, self.config.session_duration)
            simulator.simulate_engagement()

            # Persist cookies for the next session on this domain
            cookie_mgr.save()

        finally:
            if driver:
                driver.quit()

    def _print_summary(self, duration: float):
        total = self.sessions_completed + self.sessions_failed
        logger.info("\n" + "=" * 60)
        logger.info("EXECUTION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Sessions Completed : {self.sessions_completed}")
        logger.info(f"Sessions Failed    : {self.sessions_failed}")
        logger.info(f"Total Duration     : {duration:.2f}s ({duration / 60:.2f}m)")
        if total > 0:
            logger.info(f"Success Rate       : {self.sessions_completed / total * 100:.1f}%")

        # Per-URL breakdown
        if len(self._url_stats) > 1:
            logger.info("-" * 60)
            logger.info("PER-URL BREAKDOWN")
            logger.info("-" * 60)
            for url, counts in self._url_stats.items():
                url_total = counts["completed"] + counts["failed"]
                rate = (counts["completed"] / url_total * 100) if url_total > 0 else 0
                logger.info(
                    f"  {url}\n"
                    f"    Completed: {counts['completed']}  Failed: {counts['failed']}  "
                    f"Success: {rate:.1f}%"
                )

        logger.info("=" * 60)
