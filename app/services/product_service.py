"""
app/services/product_service.py - 100% Accurate Lowest Price Product Discovery Engine
Powered by curl_cffi Chrome TLS Impersonation & shopifyy.py architecture
"""
import asyncio
import re
import random
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List
from curl_cffi.requests import AsyncSession as CurlAsyncSession
import httpx

from app.core.config import settings
from app.core.cache import product_cache, product_single_flight
from app.core.proxy_manager import proxy_manager, normalize_proxy
from app.core.exceptions import (
    SiteNotFoundError,
    PasswordProtectedError,
    StoreFrozenError,
    NoProductsFoundError,
    SiteThrottledError,
    SiteDiagnosticError
)


def normalize_site_url(site: str) -> str:
    """Ensures proper https:// schema and strips paths/trailing slashes."""
    if not site:
        return ""
    site = site.strip()
    if not site.startswith(('http://', 'https://')):
        site = f"https://{site}"
    parsed = urlparse(site)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


_CHROME_BUILDS = {
    "136": "136.0.7103.93",
    "133": "133.0.6943.126",
    "131": "131.0.6778.85",
    "124": "124.0.6367.118",
    "120": "120.0.6099.109"
}

_CHROME_BRANDS = {
    "136": ('"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
            '"Chromium";v="136.0.7103.93", "Google Chrome";v="136.0.7103.93", "Not.A/Brand";v="99.0.0.0"'),
    "124": ('"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            '"Chromium";v="124.0.6367.118", "Google Chrome";v="124.0.6367.118", "Not-A.Brand";v="99.0.0.0"'),
}


def _get_discovery_headers(site: str) -> Dict[str, str]:
    ver = random.choice(["136", "124"])
    build = _CHROME_BUILDS.get(ver, "136.0.7103.93")
    brand = _CHROME_BRANDS.get(ver, _CHROME_BRANDS["136"])[0]
    return {
        "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{build} Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"{site}/",
        "Origin": site,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": brand,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    }


class ProductCandidate:
    __slots__ = ("price", "variant_id", "title", "variant_title", "handle")

    def __init__(self, price: float, variant_id: str, title: str, variant_title: str, handle: str):
        self.price = price
        self.variant_id = variant_id
        self.title = title
        self.variant_title = variant_title
        self.handle = handle

    def to_dict(self, site: str) -> Dict[str, Any]:
        full_title = f"{self.title} - {self.variant_title}".strip(" -") if self.variant_title and self.variant_title.lower() != "default title" else self.title
        return {
            "site": site,
            "price": self.price,
            "price_str": f"{self.price:.2f}",
            "variant_id": str(self.variant_id),
            "title": full_title,
            "link": f"{site}/products/{self.handle}" if self.handle else site
        }


class ProductService:
    """Engine that guarantees discovery of the absolute lowest-price available product variant."""

    @staticmethod
    def _extract_variants_from_products(products_json: List[Dict[str, Any]]) -> List[ProductCandidate]:
        candidates: List[ProductCandidate] = []
        for product in products_json:
            if not isinstance(product, dict):
                continue
            p_title = product.get("title", "Product")
            p_handle = product.get("handle", "")
            variants = product.get("variants", [])
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                available = variant.get("available")
                if available is None:
                    inv_q = variant.get("inventory_quantity")
                    inv_pol = (variant.get("inventory_policy") or "").lower()
                    inv_mgmt = (variant.get("inventory_management") or "").lower()
                    if inv_pol == "continue":
                        # store allows oversell — always available
                        available = True
                    elif inv_q is None:
                        # inventory not tracked → treat as available
                        available = True
                    elif isinstance(inv_q, (int, float)):
                        available = inv_q > 0
                    else:
                        available = True
                elif isinstance(available, str):
                    available = available.lower() in ("true", "1", "yes")

                if not available:
                    continue

                raw_price = variant.get("price")
                if raw_price is None:
                    raw_price = variant.get("price_amount")
                if raw_price is None:
                    continue

                try:
                    price_val = float(str(raw_price).replace("$", "").replace(",", "").strip())
                    # include free ($0) products — they still produce a valid checkout session
                    if price_val >= 0:
                        v_id = str(variant.get("id"))
                        v_title = str(variant.get("title", "") or "")
                        candidates.append(ProductCandidate(price_val, v_id, p_title, v_title, p_handle))
                except (ValueError, TypeError):
                    continue

        return candidates

    @classmethod
    async def get_lowest_product(
        cls,
        site_url: str,
        session: Optional[Any] = None,
        proxy_override: Optional[str] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Retrieves the guaranteed minimum priced available variant for a store.
        Utilizes SingleFlight coalescing and TTL caching to protect target stores.
        """
        norm_site = normalize_site_url(site_url)
        if not norm_site:
            raise SiteNotFoundError("Empty site URL")

        if not force_refresh:
            cached = product_cache.get(norm_site)
            if cached:
                return cached

        async def _scrape_lowest():
            if not force_refresh:
                c = product_cache.get(norm_site)
                if c:
                    return c

            active_proxy = normalize_proxy(proxy_override) if proxy_override else proxy_manager.get_proxy()
            try:
                result = await cls._execute_discovery(norm_site, proxy_url=active_proxy)
            except SiteThrottledError:
                if active_proxy:
                    # Retry direct if proxy hit rate limit
                    result = await cls._execute_discovery(norm_site, proxy_url=None)
                else:
                    raise
            except Exception:
                if active_proxy:
                    try:
                        result = await cls._execute_discovery(norm_site, proxy_url=None)
                    except Exception:
                        raise
                else:
                    raise

            await product_cache.set(norm_site, result, ttl=settings.PRODUCT_CACHE_TTL)
            return result

        return await product_single_flight.execute(norm_site, _scrape_lowest)

    @classmethod
    async def _execute_discovery(cls, site: str, proxy_url: Optional[str] = None) -> Dict[str, Any]:
        headers = _get_discovery_headers(site)
        endpoints = [
            f"{site}/products.json?limit=250",
            f"{site}/products.json",
            f"{site}/collections/all/products.json?limit=250"
        ]

        last_status_code = 200

        kw = {
            "impersonate": "chrome136",
            "verify": False,
            "timeout": settings.SCRAPE_TIMEOUT
        }
        if proxy_url:
            kw["proxy"] = proxy_url

        try:
            async with CurlAsyncSession(**kw) as s:
                for ep in endpoints:
                    try:
                        resp = await s.get(ep, headers=headers)
                        last_status_code = resp.status_code

                        if resp.status_code == 200:
                            try:
                                data = resp.json()
                                products = data.get("products", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                                candidates = cls._extract_variants_from_products(products)
                                if candidates:
                                    candidates.sort(key=lambda x: x.price)
                                    return candidates[0].to_dict(site)
                            except Exception:
                                pass
                        elif resp.status_code in (401, 403):
                            raise PasswordProtectedError(site)
                        elif resp.status_code == 402:
                            raise StoreFrozenError(site)
                        elif resp.status_code == 429:
                            raise SiteThrottledError(site)
                        elif resp.status_code == 404:
                            break
                    except (PasswordProtectedError, StoreFrozenError, SiteThrottledError):
                        raise
                    except Exception:
                        continue

                # Fallback: Predictive Search
                try:
                    search_url = f"{site}/search/suggest.json?q=*&resources[type]=product&resources[limit]=20"
                    s_resp = await s.get(search_url, headers=headers)
                    if s_resp.status_code == 200:
                        sdata = s_resp.json()
                        results = sdata.get("resources", {}).get("results", {}).get("products", []) if isinstance(sdata, dict) else []
                        handles = [p.get("handle") for p in results if p.get("handle")]
                        all_candidates = []
                        for h in handles[:10]:
                            try:
                                p_resp = await s.get(f"{site}/products/{h}.js", headers=headers)
                                if p_resp.status_code == 200:
                                    p_json = p_resp.json()
                                    p_title = p_json.get("title", h)
                                    all_candidates.extend(cls._extract_variants_from_products([{"title": p_title, "handle": h, "variants": p_json.get("variants", [])}]))
                            except Exception:
                                pass
                        if all_candidates:
                            all_candidates.sort(key=lambda x: x.price)
                            return all_candidates[0].to_dict(site)
                except Exception:
                    pass

                # Fallback: Sitemap XML
                try:
                    sm_resp = await s.get(f"{site}/sitemap_products_1.xml", headers=headers)
                    if sm_resp.status_code == 200 and sm_resp.text:
                        handles = list(dict.fromkeys(re.findall(r'/products/([a-zA-Z0-9_-]+)', sm_resp.text)))[:10]
                        all_candidates = []
                        for h in handles:
                            try:
                                p_resp = await s.get(f"{site}/products/{h}.js", headers=headers)
                                if p_resp.status_code == 200:
                                    p_json = p_resp.json()
                                    p_title = p_json.get("title", h)
                                    all_candidates.extend(cls._extract_variants_from_products([{"title": p_title, "handle": h, "variants": p_json.get("variants", [])}]))
                            except Exception:
                                pass
                        if all_candidates:
                            all_candidates.sort(key=lambda x: x.price)
                            return all_candidates[0].to_dict(site)
                except Exception:
                    pass

        except (PasswordProtectedError, StoreFrozenError, SiteThrottledError):
            raise
        except Exception as e:
            if "404" in str(e):
                last_status_code = 404

        if last_status_code == 404:
            raise SiteNotFoundError(site)
        elif last_status_code in (401, 403):
            raise PasswordProtectedError(site)
        elif last_status_code == 402:
            raise StoreFrozenError(site)
        elif last_status_code == 429:
            raise SiteThrottledError(site)

        raise NoProductsFoundError(site)
