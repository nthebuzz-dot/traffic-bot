from bot.logger import setup_logger

logger = setup_logger(__name__)

class ProxyManager:
    """Manage proxy rotation"""
    
    def __init__(self, proxy_list=None):
        """
        Initialize proxy manager
        
        Args:
            proxy_list: List of proxy URLs
        """
        self.proxy_list = proxy_list or []
        self.current_index = 0
        
        if self.proxy_list:
            logger.info(f"Initialized ProxyManager with {len(self.proxy_list)} proxies")
        else:
            logger.info("No proxies configured — running without proxy")
    
    def get_next_proxy(self):
        """
        Get next proxy in rotation
        
        Returns:
            Proxy URL or None if no proxies available
        """
        if not self.proxy_list:
            return None
        
        proxy = self.proxy_list[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.proxy_list)
        
        logger.debug(f"Rotating to proxy: {proxy}")
        return proxy
    
    def has_proxies(self):
        """Check if proxies are available"""
        return len(self.proxy_list) > 0
