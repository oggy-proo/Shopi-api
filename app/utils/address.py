"""
app/utils/address.py - Localized Address Book & Realistic Buyer Identity Generator
"""
import random
from urllib.parse import urlparse
from typing import Dict, Tuple, Optional
from app.core.config import settings

FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph",
    "Thomas", "Charles", "Daniel", "Matthew", "Anthony", "Donald", "Mark", "Paul",
    "Steven", "Andrew", "Kenneth", "Joshua", "Kevin", "Brian", "George", "Edward",
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica",
    "Sarah", "Karen", "Lisa", "Nancy", "Betty", "Margaret", "Sandra", "Ashley", "Emily"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White"
]

EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "icloud.com", "protonmail.com"
]

# TLD to ISO Country mapping
TLD_COUNTRY_MAP = {
    "UK": "UK",
    "GB": "UK",
    "CA": "CA",
    "AU": "AU",
    "US": "US",
    "COM": "US",
    "ORG": "US",
    "NET": "US",
    "IO": "US",
    "CO": "US"
}


def get_random_name() -> Tuple[str, str]:
    """Returns random (first_name, last_name)."""
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)


def generate_email(first_name: str, last_name: str) -> str:
    """Generates realistic buyer email with salt."""
    domain = random.choice(EMAIL_DOMAINS)
    rand_num = random.randint(10, 999)
    return f"{first_name.lower()}.{last_name.lower()}{rand_num}@{domain}"


def get_address_for_site(site_url: str, country_code: Optional[str] = None) -> Dict[str, str]:
    """Selects the best localized shipping address matching the store's TLD/country or specified country_code."""
    if country_code:
        c_upper = country_code.upper()
        if c_upper in settings.ADDRESS_BOOK:
            return settings.ADDRESS_BOOK[c_upper].copy()
        if c_upper == "GB" and "UK" in settings.ADDRESS_BOOK:
            return settings.ADDRESS_BOOK["UK"].copy()
    try:
        netloc = urlparse(site_url).netloc
        tld = netloc.split('.')[-1].upper()
        country_key = TLD_COUNTRY_MAP.get(tld, "DEFAULT")
        return settings.ADDRESS_BOOK.get(country_key, settings.ADDRESS_BOOK["DEFAULT"]).copy()
    except Exception:
        return settings.ADDRESS_BOOK["DEFAULT"].copy()
