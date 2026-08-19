"""Exponential-backoff retry decorator with jitter — handles transient network hiccups smoothly."""
import time
import random
import logging
from functools import wraps
from typing import Callable, Tuple, Type, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_RETRYABLE: Tuple[Type[Exception], ...] = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.HTTPError,
)

NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 405, 410, 422}


def is_retryable(exc: Exception) -> bool:
    """Check whether the exception is genuinely transient and worth retrying."""
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        if exc.response.status_code in NON_RETRYABLE_STATUS_CODES:
            return False
    return True


def retry(
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[Exception], ...] = DEFAULT_RETRYABLE,
    on_retry: Optional[Callable] = None,
):
    """Decorator to retry function execution on transient network failures.

    Args:
        max_retries: Maximum number of retry attempts before giving up.
        base_delay: Initial sleep duration in seconds.
        max_delay: Upper limit cap on sleep duration.
        backoff_factor: Multiplier applied to delay after each failed attempt.
        jitter: If True, add random 0-1s delay so simultaneous calls do not clash together.
        retryable_exceptions: Tuple of exception types to catch and retry.
        on_retry: Optional callback(exc, attempt, delay) invoked prior to each retry.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as exc:
                    if not is_retryable(exc):
                        raise

                    if attempt == max_retries:
                        logger.error(
                            "Max retries (%d) exceeded for %s: %s",
                            max_retries,
                            func.__name__,
                            exc,
                        )
                        raise

                    sleep_time = min(delay, max_delay)
                    if jitter:
                        sleep_time += random.uniform(0, 1)

                    logger.warning(
                        "Retry %d/%d for %s in %.2fs: %s",
                        attempt,
                        max_retries,
                        func.__name__,
                        sleep_time,
                        exc,
                    )

                    if on_retry:
                        on_retry(exc, attempt, sleep_time)

                    time.sleep(sleep_time)
                    delay *= backoff_factor

        return wrapper

    return decorator
