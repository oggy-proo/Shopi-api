"""
app/core/proxy_manager.py - Resilient Proxy Pool & Multi-Format Proxy Normalizer
"""
import itertools
import os
import threading
from typing import Optional, List


def normalize_proxy(proxy_str: Optional[str]) -> Optional[str]:
    """
    Parses and standardizes any proxy format into a valid URI:
    - ip:port:user:pass  -> http://user:pass@ip:port
    - user:pass:ip:port  -> http://user:pass@ip:port
    - ip:port            -> http://ip:port
    - http://... / socks5://... -> unchanged
    """
    if not proxy_str:
        return None
    
    proxy_str = str(proxy_str).strip()
    if not proxy_str:
        return None

    if proxy_str.startswith(('http://', 'https://', 'socks5://', 'socks4://')):
        return proxy_str

    if '@' in proxy_str:
        return f"http://{proxy_str}"

    parts = proxy_str.split(':')
    if len(parts) == 2:
        ip, port = parts
        return f"http://{ip}:{port}"
    elif len(parts) == 4:
        # Check if first part looks like an IP address
        if parts[0].replace('.', '').isdigit():
            ip, port, user, password = parts
        else:
            user, password, ip, port = parts
        return f"http://{user}:{password}@{ip}:{port}"

    return f"http://{proxy_str}"


class ProxyManager:
    """Thread-safe round-robin proxy rotator with dynamic pool management."""
    def __init__(self, proxy_file: Optional[str] = None):
        self._proxies: List[str] = []
        self._iterator = None
        self._lock = threading.Lock()
        if proxy_file and os.path.exists(proxy_file):
            self.load_from_file(proxy_file)

    def load_from_file(self, file_path: str) -> int:
        loaded = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    norm = normalize_proxy(line.strip())
                    if norm:
                        loaded.append(norm)
            with self._lock:
                self._proxies = loaded
                self._iterator = itertools.cycle(self._proxies) if self._proxies else None
        except Exception:
            pass
        return len(self._proxies)

    def add_proxy(self, proxy_str: str) -> None:
        norm = normalize_proxy(proxy_str)
        if norm:
            with self._lock:
                if norm not in self._proxies:
                    self._proxies.append(norm)
                    self._iterator = itertools.cycle(self._proxies)

    def get_proxy(self, override: Optional[str] = None) -> Optional[str]:
        """Returns override proxy if specified, otherwise the next rotating proxy."""
        if override:
            return normalize_proxy(override)
        with self._lock:
            if not self._iterator or not self._proxies:
                return None
            return next(self._iterator)

    @property
    def total_count(self) -> int:
        return len(self._proxies)


proxy_manager = ProxyManager(os.getenv("PROXY_FILE", "ProxyPremium.txt"))
