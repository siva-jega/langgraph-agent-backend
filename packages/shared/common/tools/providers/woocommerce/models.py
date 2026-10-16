"""Pydantic models for WooCommerce provider (products, orders, AI validation)."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MediaImage(BaseModel):
    id: Optional[str] = None
    url: str
    alt: Optional[str] = None


class WooCommerceVariant(BaseModel):
    id: str
    name: Optional[str] = None
    permalink: Optional[str] = None
    regular_price: Optional[str] = None
    sale_price: Optional[str] = None
    stock_status: Optional[str] = None
    stock_quantity: Optional[int] = None
    purchasable: Optional[bool] = None
    attributes: Optional[Any] = None
    image: Optional[str] = None


class WooCommerceProduct(BaseModel):
    id: str
    name: str
    slug: Optional[str] = None
    on_sale: Optional[bool] = None
    permalink: Optional[str] = None
    price: Optional[str] = None
    stock_status: Optional[str] = None
    purchasable: Optional[bool] = None
    image_urls: Optional[List[str]] = None
    variants: Optional[List[WooCommerceVariant]] = None
    reason_of_selection: Optional[str] = None


class GetProductsResult(BaseModel):
    success: bool
    message: str
    return_direct: bool = True
    products: List[WooCommerceProduct]


class WooCommerceOrder(BaseModel):
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


class GetOrderStatusResult(BaseModel):
    success: bool
    message: str
    order: Optional[WooCommerceOrder] = None
    orders: Optional[List[WooCommerceOrder]] = None


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
    reasoning: str = Field(
        description="Brief explanation of why this product is or isn't relevant"
    )


class ProductValidationResult(BaseModel):
    evaluated_products: List[ProductRelevanceScore] = Field(
        description="List of product relevance evaluations"
    )
