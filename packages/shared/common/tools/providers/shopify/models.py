from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class MediaImage(BaseModel):
    id: Optional[str] = None
    url: str
    alt: Optional[str] = None


class ShopifyProduct(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    product_url: Optional[str] = None
    total_inventory: Optional[int] = None
    price_range: Optional[str] = None


class GetProductsResult(BaseModel):
    success: bool
    message: str
    return_direct: bool = True
    products: List[ShopifyProduct]


class QueryTranslation(BaseModel):
    """Translation of search-query arguments into English plus detected source language."""

    detected_language: str = Field(
        description=(
            "ISO 639-1 code (e.g. 'en', 'es', 'ta', 'hi', 'ar', 'zh') of the user's "
            "original message language. Use 'en' only when the text is already English."
        )
    )
    original_user_query_en: str
    query_for_dense_search_en: str
    query_for_sparse_search_en: str
    search_title_en: str


class ShopifyOrder(BaseModel):
    id: str
    name: str
    display_financial_status: Optional[str] = None
    display_fulfillment_status: Optional[str] = None
    created_at: Optional[str] = None
    total_price: Optional[str] = None
    currency_code: Optional[str] = None
    cancelled_at: Optional[str] = None
    closed_at: Optional[str] = None
    fulfillment_orders: Optional[List[Dict[str, Any]]] = None
    fulfillments: Optional[List[Dict[str, Any]]] = None
    status_url: Optional[str] = None


class GetOrderStatusResult(BaseModel):
    success: bool
    message: str
    order: Optional[ShopifyOrder] = None
    orders: Optional[List[ShopifyOrder]] = None


class ShopifyCheckout(BaseModel):
    token: str
    cart_token: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    completed_at: Optional[str] = None
    abandoned_checkout_url: Optional[str] = None
    total_price: Optional[str] = None
    currency: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    line_items: Optional[List[Dict[str, Any]]] = None


class GetCheckoutResult(BaseModel):
    success: bool
    message: str
    checkout: Optional[ShopifyCheckout] = None
    is_abandoned: bool = False


class ProductRelevanceScore(BaseModel):
    product_id: str = Field(description="The unique identifier of the product")
    is_relevant: bool = Field(
        description="Whether this product is relevant to the user's query"
    )
    relevance_score: float = Field(
        description="Relevance score from 0.0 to 1.0, where 1.0 is most relevant",
        ge=0.0,
        le=1.0,
    )


class ProductValidationResult(BaseModel):
    evaluated_products: List[ProductRelevanceScore] = Field(
        description="List of product relevance evaluations"
    )
