"""
app/core/session_pool.py - High-Performance AsyncSession Factory & Concurrency Limiter
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, AsyncGenerator
from curl_cffi.requests import AsyncSession
from app.core.config import settings
from app.core.proxy_manager import normalize_proxy

# Global Semaphore to safeguard system file descriptors and sockets under extreme loads
_concurrency_gate = asyncio.Semaphore(settings.GLOBAL_CONCURRENCY_LIMIT)


@asynccontextmanager
async def create_client_session(
    proxy: Optional[str] = None,
    impersonate: str = settings.IMPERSONATE_BROWSER,
    verify: bool = False
) -> AsyncGenerator[AsyncSession, None]:
    """
    Spawns an optimized AsyncSession with Chrome TLS fingerprinting,
    controlled concurrency gate, and automatic resource reclamation.
    """
    clean_proxy = normalize_proxy(proxy)
    async with _concurrency_gate:
        session = AsyncSession(
            impersonate=impersonate,
            verify=verify,
            trust_env=False if clean_proxy else True
        )
        if clean_proxy:
            session.proxies = {"http": clean_proxy, "https": clean_proxy}
        try:
            yield session
        finally:
            try:
                await session.close()
            except Exception:
                pass
