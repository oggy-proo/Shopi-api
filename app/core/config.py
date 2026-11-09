"""
app/core/config.py - Centralized Configuration & Environment Settings
"""
from typing import Dict, Any
import os
from pydantic import BaseModel, Field


class Settings(BaseModel):
    # API Metadata
    PROJECT_NAME: str = "Shopify Enterprise Checker API"
    VERSION: str = "2.0.0"
    API_PREFIX: str = ""
    DEBUG: bool = False

    # Concurrency & Performance Thresholds
    GLOBAL_CONCURRENCY_LIMIT: int = Field(default=2000, description="Max simultaneous in-flight checks")
    DEFAULT_BATCH_WORKERS: int = Field(default=100, description="Default concurrency for batch card checking")
    MAX_BATCH_WORKERS: int = Field(default=500, description="Max allowed workers per batch")
    
    # Timeouts (in seconds)
    CONNECT_TIMEOUT: float = 8.0
    REQUEST_TIMEOUT: float = 25.0
    SCRAPE_TIMEOUT: float = 7.0
    
    # Caching TTL (in seconds)
    PRODUCT_CACHE_TTL: int = 7200       # 2 Hours for lowest price product per store
    SITE_STATUS_CACHE_TTL: int = 1800   # 30 Minutes for site errors/state
    
    # Browser Impersonation
    IMPERSONATE_BROWSER: str = "chrome120"
    
    # Files
    PROXY_FILE: str = os.getenv("PROXY_FILE", "ProxyPremium.txt")
    SITES_FILE: str = os.getenv("SITES_FILE", "sites.txt")

    # Shipping/Billing Address Defaults by Country/TLD
    ADDRESS_BOOK: Dict[str, Dict[str, str]] = {
        "US": {
            "address1": "4024 College Point Blvd",
            "city": "Flushing",
            "postalCode": "11354",
            "zoneCode": "NY",
            "countryCode": "US",
            "phone": "2194157586"
        },
        "UK": {
            "address1": "10 Downing Street",
            "city": "London",
            "postalCode": "SW1A 2AA",
            "zoneCode": "ENG",
            "countryCode": "GB",
            "phone": "7911123456"
        },
        "CA": {
            "address1": "200 University Ave",
            "city": "Toronto",
            "postalCode": "M5H 3C6",
            "zoneCode": "ON",
            "countryCode": "CA",
            "phone": "4165550192"
        },
        "AU": {
            "address1": "100 Queen Street",
            "city": "Melbourne",
            "postalCode": "3000",
            "zoneCode": "VIC",
            "countryCode": "AU",
            "phone": "412345678"
        },
        "DEFAULT": {
            "address1": "123 Main St",
            "city": "New York",
            "postalCode": "10001",
            "zoneCode": "NY",
            "countryCode": "US",
            "phone": "2194157586"
        }
    }


settings = Settings()
