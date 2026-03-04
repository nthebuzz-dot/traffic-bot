import threading
import time
from concurrent.futures import ThreadPoolExecutor

from bot.logger import setup_logger
from bot.proxy_manager import ProxyManager
from bot.selenium_driver import SeleniumDriver, resolve_driver_once
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
    """

    def __init__(self, config):
        self.config = config
        self.proxy_manager = ProxyManager(config.proxies)
        self.sessions_completed = 0
        self.sessions_failed = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self):
        global _STOP_REQUESTED
        _STOP_REQUESTED = False  # reset on each run

        concurrency = max(1, self.config.concurrent_sessions)

        logger.info("=" * 60)
        logger.info("WEB TRAFFIC BOT STARTED")
        logger.info("=" * 60)
        logger.info(f"Target URL          : {self.config.target_url}")
        logger.info(f"Total Sessions      : {self.config.sessions_count}")
        logger.info(f"Concurrent Sessions : {concurrency}")
        logger.info(f"Session Duration    : {self.config.session_duration}s")
        logger.info(f"Total Duration      : {self.config.duration_seconds}s")
        logger.info(f"Proxies             : {len(self.config.proxies)}")
        logger.info(f"Headless            : {self.config.headless}")
        logger.info("=" * 60)

        # Pre-resolve chromedriver ONCE before the thread pool starts.
        # This prevents race conditions when many concurrent sessions all try
        # to download/access the driver cache simultaneously.
        pre_driver_path = resolve_driver_once(self.config.chromium_path)
        if pre_driver_path:
            logger.info(f"ChromeDriver pre-resolved: {pre_driver_path}")

        start_time = time.time()

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

                # Submit up to `concurrency` sessions at once
                while (len(futures) < concurrency
                       and session_num < self.config.sessions_count
                       and not _STOP_REQUESTED):
                    session_num += 1
                    future = pool.submit(self._run_session, session_num, pre_driver_path)
                    futures[future] = session_num
                    logger.info(f"[Session {session_num}/{self.config.sessions_count}] started "
                                f"(active: {len(futures)})")

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
                        snum = futures.pop(f)
                        try:
                            f.result()  # re-raise any exception
                            with _counter_lock:
                                self.sessions_completed += 1
                            logger.info(f"[Session {snum}] completed ✓")
                        except Exception as e:
                            with _counter_lock:
                                self.sessions_failed += 1
                            logger.error(f"[Session {snum}] failed: {e}")

            # Wait for all remaining in-flight sessions to finish
            logger.info("Waiting for in-flight sessions to finish...")
            for f, snum in list(futures.items()):
                try:
                    f.result()
                    with _counter_lock:
                        self.sessions_completed += 1
                    logger.info(f"[Session {snum}] completed ✓")
                except Exception as e:
                    with _counter_lock:
                        self.sessions_failed += 1
                    logger.error(f"[Session {snum}] failed: {e}")

        self._print_summary(time.time() - start_time)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_session(self, session_num: int, pre_driver_path=None):
        """Run a single browser session. Called from a thread pool worker."""
        driver = None
        try:
            proxy = self.proxy_manager.get_next_proxy()
            if proxy:
                logger.info(f"[Session {session_num}] Using proxy: {proxy}")

            driver = SeleniumDriver(
                headless=self.config.headless,
                proxy=proxy,
                chromium_path=self.config.chromium_path,
                driver_path=pre_driver_path,
            )
            driver.get(self.config.target_url)

            simulator = SessionSimulator(driver.driver, self.config.session_duration)
            simulator.simulate_engagement()

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
        logger.info("=" * 60)
