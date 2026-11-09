"""
app/utils/parser.py - High-Precision Credit Card & Shopify Error Code Parsers
"""
import re
from typing import Dict, Any, Optional
from app.core.exceptions import InvalidCardFormatError


def parse_cc_string(cc_input: str) -> Dict[str, str]:
    """
    Parses card input in format: CC|MM|YYYY|CVV or CC/MM/YY/CVV
    Automatically normalizes 2-digit years to 4-digit years.
    """
    if not cc_input or not isinstance(cc_input, str):
        raise InvalidCardFormatError("Empty card string provided")

    # Match CC, MM, YY/YYYY, CVV with various delimiters
    regex = r'(\d{13,19})[\s|:/]+(\d{1,2})[\s|:/]+(\d{2,4})[\s|:/]+(\d{3,4})'
    match = re.search(regex, cc_input.strip())
    if not match:
        raise InvalidCardFormatError(f"Cannot parse '{cc_input}'. Expected CC|MM|YYYY|CVV")

    cc, mm, yy, cvv = match.groups()

    # Normalize month
    mm_int = int(mm)
    if mm_int < 1 or mm_int > 12:
        raise InvalidCardFormatError(f"Invalid expiry month '{mm}'. Must be 1-12")
    mm = f"{mm_int:02d}"

    # Normalize year
    if len(yy) == 2:
        yy = f"20{yy}"
    yy_int = int(yy)
    from datetime import datetime
    current_year = datetime.now().year
    if yy_int < current_year or yy_int > current_year + 20:
        raise InvalidCardFormatError(f"Invalid expiry year '{yy}'")

    return {
        "cc": cc,
        "mes": mm,
        "ano": yy,
        "cvv": cvv
    }


def extract_between(text: Optional[str], start: str, end: str) -> Optional[str]:
    """Extracts text strictly between start and end substrings."""
    if not text or not start or not end:
        return None
    try:
        if start in text:
            parts = text.split(start, 1)
            if len(parts) > 1 and end in parts[1]:
                res = parts[1].split(end, 1)[0].strip()
                return res if res else None
    except Exception:
        pass
    return None


def is_captcha_challenge(text: Optional[str]) -> bool:
    """Detects if response contains a Captcha challenge."""
    if not text:
        return False
    indicators = [
        'CAPTCHA_REQUIRED',
        '"code":"CAPTCHA_REQUIRED"',
        "'code':'CAPTCHA_REQUIRED'",
        'captcha required',
        'CAPTCHA CHALLENGE',
        'hcaptcha',
        'h-captcha',
        'cf-challenge',
        'challenge-form'
    ]
    text_upper = text.upper()
    return any(ind.upper() in text_upper for ind in indicators)


def extract_clean_response(message: Any) -> str:
    """Extracts standardized decline/error code from messages or JSON payloads."""
    if not message:
        return "CARD_DECLINED"

    msg_str = str(message).strip()
    if not msg_str or msg_str in ('""', "''", 'null', 'None'):
        return "CARD_DECLINED"

    # Priority 1: Known standard merchant / card processor error codes
    standard_codes = [
        "CARD_DECLINED",
        "INSUFFICIENT_FUNDS",
        "INCORRECT_CVC",
        "EXPIRED_CARD",
        "INCORRECT_ZIP",
        "CALL_ISSUER",
        "PICK_UP_CARD",
        "DO_NOT_HONOR",
        "INVALID_ACCOUNT",
        "RESTRICTED_CARD",
        "PROCESSING_ERROR",
        "TESTMODE_CHARGES_ONLY",
        "TRANSACTION_NOT_ALLOWED",
        "ORDER_PLACED",
        "OTP_REQUIRED",
        "AUTHENTICATION_REQUIRED",
        "CAPTCHA_REQUIRED",
        "LOGIN_REQUIRED",
        "PASSWORD_PROTECTED",
        "OUT_OF_STOCK",
        "SITE_ERROR",
        "THROTTLED"
    ]
    for code in standard_codes:
        if code in msg_str.upper():
            return code

    # Priority 2: Extract PAYMENTS_* or CARD_* or uppercase underscores
    patterns = [
        r'(PAYMENTS_[A-Z_]+)',
        r'(CARD_[A-Z_]+)',
        r'([A-Z]{3,}_[A-Z_]{3,})',
        r'{"code":"([^"]+)"}',
        r"'code':'([^']+)'"
    ]
    for pat in patterns:
        matches = re.findall(pat, msg_str)
        if matches:
            first = matches[0]
            if isinstance(first, tuple):
                first = first[0]
            cleaned = first.strip("{}:'\" ")
            if cleaned and len(cleaned) < 40:
                return cleaned

    # Priority 3: First meaningful snippet
    words = msg_str.split()
    if words and "_" in words[0] and words[0].isupper():
        return words[0]

    cleaned = msg_str[:45].strip()
    return cleaned if cleaned else "CARD_DECLINED"
