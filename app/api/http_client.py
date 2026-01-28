"""
Shared HTTP client utilities for internal service calls.

Provides consistent timeout configuration, retry logic with exponential backoff,
and connection pooling for improved performance.
"""

import asyncio
import logging
from typing import Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

# Timeout configuration for internal service calls
# - connect: Time to establish TCP connection
# - read: Time to receive response after request sent
# - write: Time to send request data
# - pool: Time to acquire connection from pool
INTERNAL_SERVICE_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=30.0,
    write=30.0,
    pool=10.0,
)

# Connection pool limits
CONNECTION_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 10.0  # seconds

# Module-level shared client instance
_shared_client: httpx.AsyncClient | None = None


async def get_shared_client() -> httpx.AsyncClient:
    """Get or create a shared HTTP client with connection pooling.

    The shared client maintains a connection pool for better performance
    when making multiple requests to internal services.

    Returns:
        httpx.AsyncClient: The shared HTTP client instance.
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=INTERNAL_SERVICE_TIMEOUT,
            limits=CONNECTION_LIMITS,
        )
    return _shared_client


async def close_shared_client() -> None:
    """Close the shared HTTP client.

    Should be called during application shutdown to clean up resources.
    """
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None


T = TypeVar("T")


def _get_notify_exception() -> Callable:
    """Lazily import notify_exception to avoid circular imports."""
    from app.slack.buglog_notifier import notify_exception

    return notify_exception


async def retry_on_timeout(
    func,
    *args,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    notify_on_final_failure: bool = True,
    **kwargs,
) -> T:
    """Execute an async function with retry logic for timeout errors.

    Implements exponential backoff for httpx.ConnectTimeout and httpx.ReadTimeout
    errors. Other exceptions are raised immediately without retry.

    Args:
        func: The async function to execute.
        *args: Positional arguments to pass to the function.
        max_retries: Maximum number of retry attempts (default: 3).
        base_delay: Initial delay in seconds between retries (default: 1.0).
        max_delay: Maximum delay in seconds between retries (default: 10.0).
        notify_on_final_failure: Whether to notify buglog on final failure (default: True).
        **kwargs: Keyword arguments to pass to the function.

    Returns:
        The return value of the function.

    Raises:
        httpx.ConnectTimeout: If all retry attempts fail due to connect timeout.
        httpx.ReadTimeout: If all retry attempts fail due to read timeout.
        Exception: Any other exception raised by the function.
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last_exception = e
            if attempt < max_retries:
                # Calculate exponential backoff: base_delay * (2 ^ attempt)
                delay = min(base_delay * (2**attempt), max_delay)
                logger.warning(
                    f"Timeout error on attempt {attempt + 1}/{max_retries + 1}, "
                    f"retrying in {delay:.1f}s: {type(e).__name__}"
                )
                await asyncio.sleep(delay)
                continue
            # Final attempt failed
            if notify_on_final_failure:
                notify_exception = _get_notify_exception()
                notify_exception(
                    msg=f"HTTP request failed after {max_retries + 1} attempts",
                    exc=e,
                    extra={"attempts": max_retries + 1, "error_type": type(e).__name__},
                )
            raise
        except httpx.ConnectError as e:
            # Connection errors (DNS, refused, etc.) - also worth retrying
            last_exception = e
            if attempt < max_retries:
                delay = min(base_delay * (2**attempt), max_delay)
                logger.warning(
                    f"Connection error on attempt {attempt + 1}/{max_retries + 1}, "
                    f"retrying in {delay:.1f}s: {type(e).__name__}"
                )
                await asyncio.sleep(delay)
                continue
            if notify_on_final_failure:
                notify_exception = _get_notify_exception()
                notify_exception(
                    msg=f"HTTP connection failed after {max_retries + 1} attempts",
                    exc=e,
                    extra={"attempts": max_retries + 1, "error_type": type(e).__name__},
                )
            raise

    # This should not be reached, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected state in retry_on_timeout")
