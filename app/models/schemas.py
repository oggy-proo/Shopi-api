"""
app/models/schemas.py - Modern Pydantic v2 Request & Response Data Contracts
"""
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field, field_validator


class CardCheckRequest(BaseModel):
    """Schema for single card check via JSON POST."""
    site: str = Field(..., description="Shopify store domain or URL (e.g. store.myshopify.com)", examples=["https://02c8ee-2.myshopify.com"])
    cc: Optional[str] = Field(None, description="Credit card in CC|MM|YYYY|CVV format (optional for gateway/price inspection)")
    proxy: Optional[str] = Field(None, description="Optional custom proxy (ip:port, user:pass@ip:port, socks5://...)")
    variant: Optional[str] = Field(None, description="Optional specific product variant ID. If omitted, lowest price is used.")


class BatchCardCheckRequest(BaseModel):
    """Schema for ultra high-speed batch card verification."""
    site: str = Field(..., description="Target Shopify store domain or URL")
    cards: List[str] = Field(..., min_length=1, max_length=5000, description="List of card strings")
    proxy: Optional[str] = Field(None, description="Optional proxy to route requests through")
    concurrency: int = Field(default=50, ge=1, le=500, description="Concurrent checking workers")


class CardCheckResponse(BaseModel):
    """Standardized result schema adhering 100% to API response specifications."""
    Gateway: str = Field(..., description="Discovered or detected payment processor")
    Price: float = Field(..., description="Total charged price including shipping and taxes")
    Product_Price: float = Field(0.0, description="Base price of lowest product selected")
    Product_Title: str = Field("", description="Title of lowest product selected")
    Shipping_Price: float = Field(0.0, description="Store mandatory shipping cost")
    Response: str = Field(..., description="Normalized clean response or decline code")
    Status: bool = Field(..., description="True if card was approved/placed, False if declined/error")
    cc: str = Field("", description="Original card input string")
    time_taken: Optional[float] = Field(None, description="Time taken in seconds")


class BatchCardCheckResponse(BaseModel):
    """Aggregated batch response with metrics."""
    site: str
    total_submitted: int
    total_processed: int
    approved_count: int
    declined_count: int
    otp_count: int
    error_count: int
    elapsed_seconds: float
    results: List[CardCheckResponse]


class LowestProductResponse(BaseModel):
    """Payload for lowest price product lookup."""
    site: str
    variant_id: str
    price: float
    price_str: str
    title: str
    link: str
    cached: bool


class SiteDiagnoseResponse(BaseModel):
    """Detailed site diagnostics and automated checkout readiness."""
    site: str
    status: str
    eligible_for_check: bool
    diagnostic_message: str
    lowest_product: Optional[LowestProductResponse] = None
