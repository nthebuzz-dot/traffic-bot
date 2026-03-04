import threading

from bot.logger import setup_logger

logger = setup_logger(__name__)


class ProxyManager:
    """
    Thread-safe round-robin proxy rotation manager.

    Multiple concurrent sessions call get_next_proxy() simultaneously.
    A threading.Lock ensures the index is incremented atomically so no
    two sessions ever receive the same proxy slot at the same time.
    """

    def __init__(self, proxy_list=None):
        """
        Args:
            proxy_list: List of proxy URL strings, e.g.
                        ['http://user:pass@host:port', ...]
        """
        self.proxy_list = [p for p in (proxy_list or []) if p and p.strip()]
        self._index = 0
        self._lock = threading.Lock()

        if self.proxy_list:
            logger.info(f"ProxyManager initialised with {len(self.proxy_list)} proxies")
        else:
            logger.info("No proxies configured — running without proxy")

    def get_next_proxy(self):
        """
        Return the next proxy URL in round-robin order (thread-safe).

        Returns:
            Proxy URL string, or None if no proxies are configured.
        """
        if not self.proxy_list:
            return None

        with self._lock:
            proxy = self.proxy_list[self._index]
            self._index = (self._index + 1) % len(self.proxy_list)

        logger.debug(f"Using proxy: {proxy}")
        return proxy

    def has_proxies(self) -> bool:
        """Return True if at least one proxy is configured."""
        return bool(self.proxy_list)
