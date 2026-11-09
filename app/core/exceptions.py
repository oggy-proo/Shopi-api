"""
app/core/exceptions.py - Typed Domain Exceptions for Shopify Engine
"""

class ShopifyAPIException(Exception):
    """Base exception for all Shopify API errors."""
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class SiteDiagnosticError(ShopifyAPIException):
    """Raised when store configuration prevents automated checkout."""
    pass


class SiteNotFoundError(SiteDiagnosticError):
    def __init__(self, site: str):
        super().__init__("SITE_NOT_FOUND", f"Store does not exist or domain is invalid: {site}", 404)


class PasswordProtectedError(SiteDiagnosticError):
    def __init__(self, site: str):
        super().__init__("PASSWORD_PROTECTED", f"Store is locked with a storefront password: {site}", 403)


class StoreFrozenError(SiteDiagnosticError):
    def __init__(self, site: str):
        super().__init__("STORE_FROZEN_OR_UNPAID", f"Shopify store subscription has expired or is frozen (402): {site}", 402)


class LoginRequiredError(SiteDiagnosticError):
    def __init__(self, site: str):
        super().__init__("LOGIN_REQUIRED", f"Store requires customer account login before checkout: {site}", 403)


class NoProductsFoundError(SiteDiagnosticError):
    def __init__(self, site: str):
        super().__init__("NO_PRODUCTS_FOUND", f"Store has no active, purchasable products in stock: {site}", 404)


class CartAddError(SiteDiagnosticError):
    def __init__(self, site: str, detail: str = ""):
        super().__init__("CART_ADD_FAILED", f"Failed adding lowest variant to cart on {site}: {detail}", 400)


class NoPaymentGatewayError(SiteDiagnosticError):
    def __init__(self, site: str):
        super().__init__("NO_PAYMENT_GATEWAY", f"Store does not support direct credit card gateway on {site}", 400)


class CaptchaChallengeError(SiteDiagnosticError):
    def __init__(self, site: str):
        super().__init__("CAPTCHA_REQUIRED", f"Cloudflare or hCaptcha challenge required on {site}", 429)


class SiteThrottledError(SiteDiagnosticError):
    def __init__(self, site: str):
        super().__init__("SITE_THROTTLED", f"Shopify rate-limit throttled on {site}", 429)


class ProxyConnectionError(ShopifyAPIException):
    def __init__(self, detail: str):
        super().__init__("PROXY_CONNECTION_FAILED", f"Proxy connection error: {detail}", 502)


class InvalidCardFormatError(ShopifyAPIException):
    def __init__(self, detail: str):
        super().__init__("INVALID_CC_FORMAT", f"Invalid card format: {detail}. Expected CC|MM|YYYY|CVV", 400)
