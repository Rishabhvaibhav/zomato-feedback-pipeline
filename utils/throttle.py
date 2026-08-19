"""Request rate limiter with intelligent throttling and adaptive backoff handling."""
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Throttler:
    """Maintains mandatory waiting interval between consecutive web requests to avoid rate limits."""

    def __init__(self, delay_seconds: float = 1.5):
        self.delay = delay_seconds
        self._last_request_time: Optional[float] = None

    def sleep(self) -> None:
        """Take a pause if necessary so minimum delay requirement is respected."""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            remaining = self.delay - elapsed
            if remaining > 0:
                logger.debug("Throttling for %.2fs", remaining)
                time.sleep(remaining)
        self._last_request_time = time.time()

    def adaptive_sleep(self, response) -> None:
        """Respect server instructions if Retry-After header is received in response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                sleep_seconds = int(retry_after)
                logger.info("Server requested Retry-After: %ds", sleep_seconds)
                time.sleep(sleep_seconds)
                return
            except ValueError:
                pass
        self.sleep()
