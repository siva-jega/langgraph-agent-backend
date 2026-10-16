"""Pydantic models for WareIQ provider."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class WareIQProduct(BaseModel):
    name: str
    sku: str
    units: int
    selling_price: float
    discount: Optional[float] = None
    tax: Optional[float] = None
    hsn: Optional[str] = None


class WareIQOrder(BaseModel):
    order_id: str
    order_date: str
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    shipping_address: str
    shipping_city: str
    shipping_state: str
    shipping_pincode: str
    shipping_country: str = "India"
    order_items: List[WareIQProduct] = []
    payment_method: str = "Prepaid"
    sub_total: float
    weight: float
    length: Optional[float] = None
    breadth: Optional[float] = None
    height: Optional[float] = None


class WareIQTrackingActivity(BaseModel):
    date: Optional[str] = None
    status: Optional[str] = None
    activity: Optional[str] = None
    location: Optional[str] = None


class WareIQTrackingResult(BaseModel):
    tracking_data: Optional[Dict[str, Any]] = None
    awb_code: Optional[str] = None
    courier_name: Optional[str] = None
    current_status: Optional[str] = None
    delivered_date: Optional[str] = None
    etd: Optional[str] = None
    activities: Optional[List[WareIQTrackingActivity]] = None
