"""
app/core/cache.py - Ultra-Fast In-Memory Async TTL Cache & SingleFlight Coalescer
"""
import asyncio
import time
from typing import Any, Dict, Optional, Tuple, Callable, Coroutine, TypeVar

T = TypeVar("T")


class AsyncTTLCache:
    """High-performance, async-safe in-memory cache with TTL."""
    def __init__(self, default_ttl: int = 7200):
        self._default_ttl = default_ttl
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    def get(self, key: str) -> Optional[Any]:
        """Instant lock-free get with fast expiration check."""
        entry = self._cache.get(key)
        if not entry:
            return None
        val, expiry = entry
        if time.monotonic() > expiry:
            self._cache.pop(key, None)
            return None
        return val

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set key with TTL."""
        expiry = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        async with self._lock:
            self._cache[key] = (value, expiry)

    async def delete(self, key: str) -> None:
        """Remove key from cache."""
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """Clear all entries."""
        async with self._lock:
            self._cache.clear()

    def size(self) -> int:
        return len(self._cache)


class SingleFlight:
    """
    Prevents cache stampedes / dogpiling by coalescing duplicate concurrent calls.
    If 500 requests arrive for the same domain, only ONE executes the underlying coroutine.
    The remaining 499 await the same future and receive the identical result simultaneously.
    """
    def __init__(self):
        self._calls: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def execute(self, key: str, coro_factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
        async with self._lock:
            if key in self._calls:
                future = self._calls[key]
                # Await in-progress execution
                return await future

            # Create future for this key
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._calls[key] = future

        # Execute the coroutine once
        try:
            result = await coro_factory()
            if not future.done():
                future.set_result(result)
            return result
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            # Retrieve exception from future so asyncio does not mark it unretrieved
            future.exception()
            raise
        finally:
            async with self._lock:
                self._calls.pop(key, None)


# Global instances for product variants and site diagnostics
product_cache = AsyncTTLCache(default_ttl=7200)         # 2 hour cache
site_diagnostic_cache = AsyncTTLCache(default_ttl=1800)   # 30 min cache
product_single_flight = SingleFlight()
