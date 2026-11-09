"""
app/api/routes.py - High-Performance FastAPI Endpoints for Shopify Engine
"""
import asyncio
import time
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException, status
from fastapi.responses import JSONResponse

from app.models.schemas import (
    CardCheckRequest,
    CardCheckResponse,
    BatchCardCheckRequest,
    BatchCardCheckResponse,
    LowestProductResponse,
    SiteDiagnoseResponse
)
from app.services.checkout_service import CheckoutService
from app.services.product_service import ProductService, normalize_site_url
from app.core.proxy_manager import proxy_manager
from app.core.cache import product_cache
from app.core.exceptions import (
    ShopifyAPIException,
    SiteDiagnosticError,
    SiteNotFoundError,
    PasswordProtectedError,
    StoreFrozenError,
    NoProductsFoundError,
    InvalidCardFormatError
)

router = APIRouter()


@router.get(
    "/shopify",
    response_model=CardCheckResponse,
    summary="Legacy Query-Param Shopify Card Checker",
    description="Maintains 100% backward compatibility with existing bots and integrations."
)
async def shopify_checker_legacy(
    site: str = Query(..., description="Target Shopify URL or domain"),
    cc: Optional[str] = Query(None, description="Optional card in CC|MM|YYYY|CVV format"),
    proxy: Optional[str] = Query(None, description="Optional proxy"),
    variant: Optional[str] = Query(None, description="Optional variant ID")
):
    try:
        result = await CheckoutService.process_card(
            cc_string=cc,
            site_url=site,
            variant_id=variant,
            proxy_override=proxy
        )
        return CardCheckResponse(**result)
    except InvalidCardFormatError as icf:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "Gateway": "UNKNOWN",
                "Price": 0.0,
                "Product_Price": 0.0,
                "Product_Title": "",
                "Shipping_Price": 0.0,
                "Response": icf.code,
                "Status": False,
                "cc": cc or "",
                "error": icf.message
            }
        )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "Gateway": "UNKNOWN",
                "Price": 0.0,
                "Product_Price": 0.0,
                "Product_Title": "",
                "Shipping_Price": 0.0,
                "Response": "SERVER_ERROR",
                "Status": False,
                "cc": cc or "",
                "error": str(exc)
            }
        )


@router.post(
    "/shopify",
    response_model=CardCheckResponse,
    summary="JSON Card Verification Endpoint",
    description="High-throughput structured POST endpoint for automated card verification."
)
async def shopify_checker_post(payload: CardCheckRequest):
    try:
        result = await CheckoutService.process_card(
            cc_string=payload.cc,
            site_url=payload.site,
            variant_id=payload.variant,
            proxy_override=payload.proxy
        )
        return CardCheckResponse(**result)
    except InvalidCardFormatError as icf:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "Gateway": "UNKNOWN",
                "Price": 0.0,
                "Product_Price": 0.0,
                "Product_Title": "",
                "Shipping_Price": 0.0,
                "Response": icf.code,
                "Status": False,
                "cc": payload.cc or "",
                "error": icf.message
            }
        )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "Gateway": "UNKNOWN",
                "Price": 0.0,
                "Product_Price": 0.0,
                "Product_Title": "",
                "Shipping_Price": 0.0,
                "Response": "SERVER_ERROR",
                "Status": False,
                "cc": payload.cc or "",
                "error": str(exc)
            }
        )


@router.post(
    "/shopify/batch",
    response_model=BatchCardCheckResponse,
    summary="Concurrent High-Speed Batch Verification",
    description="Processes hundreds or thousands of cards concurrently across async worker pools."
)
async def shopify_batch_check(payload: BatchCardCheckRequest):
    start_time = time.monotonic()
    clean_site = normalize_site_url(payload.site)

    # 1. Pre-warm and resolve lowest product variant once for the entire batch
    variant_id = None
    try:
        product_info = await ProductService.get_lowest_product(
            site_url=clean_site,
            proxy_override=payload.proxy
        )
        variant_id = str(product_info["variant_id"])
    except SiteDiagnosticError as sde:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Site verification failed: {sde.message}"
        )

    # 2. Bounded concurrency execution
    semaphore = asyncio.Semaphore(payload.concurrency)

    async def _worker(card_str: str) -> CardCheckResponse:
        async with semaphore:
            try:
                res = await CheckoutService.process_card(
                    cc_string=card_str,
                    site_url=clean_site,
                    variant_id=variant_id,
                    proxy_override=payload.proxy
                )
                return CardCheckResponse(**res)
            except Exception as e:
                return CardCheckResponse(
                    Gateway="UNKNOWN",
                    Price=0.0,
                    Response="WORKER_EXCEPTION",
                    Status=False,
                    cc=card_str,
                    time_taken=0.0
                )

    tasks = [_worker(card.strip()) for card in payload.cards if card.strip()]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    approved = sum(1 for r in results if r.Status is True and r.Response == "ORDER_PLACED")
    otp = sum(1 for r in results if r.Response == "OTP_REQUIRED")
    declined = sum(1 for r in results if r.Status is True and r.Response not in ("ORDER_PLACED", "OTP_REQUIRED"))
    errors = sum(1 for r in results if r.Status is False)

    return BatchCardCheckResponse(
        site=clean_site,
        total_submitted=len(payload.cards),
        total_processed=len(results),
        approved_count=approved,
        declined_count=declined,
        otp_count=otp,
        error_count=errors,
        elapsed_seconds=round(time.monotonic() - start_time, 2),
        results=results
    )


@router.get(
    "/product/lowest",
    response_model=LowestProductResponse,
    summary="100% Accurate Lowest Product Discovery",
    description="Inspect and extract the guaranteed minimum price available variant for any Shopify store."
)
async def get_lowest_product_endpoint(
    site: str = Query(..., description="Target Shopify site"),
    force_refresh: bool = Query(False, description="Bypass cache and force fresh scraping")
):
    try:
        norm_site = normalize_site_url(site)
        is_cached = bool(product_cache.get(norm_site)) and not force_refresh
        product = await ProductService.get_lowest_product(site, force_refresh=force_refresh)
        return LowestProductResponse(
            site=product["site"],
            variant_id=str(product["variant_id"]),
            price=float(product["price"]),
            price_str=product["price_str"],
            title=product["title"],
            link=product["link"],
            cached=is_cached
        )
    except SiteNotFoundError:
        raise HTTPException(status_code=404, detail="Store not found (404)")
    except PasswordProtectedError:
        raise HTTPException(status_code=403, detail="Store is password protected")
    except StoreFrozenError:
        raise HTTPException(status_code=402, detail="Store subscription expired / frozen (402)")
    except NoProductsFoundError:
        raise HTTPException(status_code=404, detail="No active products with available inventory found on store")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/site/diagnose",
    response_model=SiteDiagnoseResponse,
    summary="Accurate Store Diagnostic & Verification Eligibility",
    description="Performs pre-flight healthcheck and status audit on a Shopify storefront."
)
async def diagnose_site_endpoint(
    site: str = Query(..., description="Target Shopify site")
):
    clean_site = normalize_site_url(site)
    try:
        prod = await ProductService.get_lowest_product(clean_site)
        lowest_dto = LowestProductResponse(
            site=prod["site"],
            variant_id=str(prod["variant_id"]),
            price=float(prod["price"]),
            price_str=prod["price_str"],
            title=prod["title"],
            link=prod["link"],
            cached=True
        )
        return SiteDiagnoseResponse(
            site=clean_site,
            status="ACTIVE",
            eligible_for_check=True,
            diagnostic_message="Store is active, reachable, and has available inventory.",
            lowest_product=lowest_dto
        )
    except PasswordProtectedError:
        return SiteDiagnoseResponse(
            site=clean_site,
            status="PASSWORD_PROTECTED",
            eligible_for_check=False,
            diagnostic_message="Store requires storefront password.",
            lowest_product=None
        )
    except StoreFrozenError:
        return SiteDiagnoseResponse(
            site=clean_site,
            status="FROZEN_OR_UNPAID",
            eligible_for_check=False,
            diagnostic_message="Store owner subscription plan expired (HTTP 402).",
            lowest_product=None
        )
    except SiteNotFoundError:
        return SiteDiagnoseResponse(
            site=clean_site,
            status="NOT_FOUND",
            eligible_for_check=False,
            diagnostic_message="Store does not exist or domain is unresolvable (HTTP 404).",
            lowest_product=None
        )
    except NoProductsFoundError:
        return SiteDiagnoseResponse(
            site=clean_site,
            status="OUT_OF_STOCK",
            eligible_for_check=False,
            diagnostic_message="Store has no available variants in inventory.",
            lowest_product=None
        )
    except Exception as exc:
        return SiteDiagnoseResponse(
            site=clean_site,
            status="ERROR",
            eligible_for_check=False,
            diagnostic_message=f"Diagnostic error: {str(exc)}",
            lowest_product=None
        )


@router.get("/health", summary="Service Health & Diagnostic Status")
async def healthcheck():
    return {
        "status": "online",
        "engine": "Shopify 1-Page Checkout v2.0",
        "proxies_available": proxy_manager.total_count,
        "cached_products": product_cache.size(),
        "timestamp": int(time.time())
    }
