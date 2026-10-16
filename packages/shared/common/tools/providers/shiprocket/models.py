"""Pydantic models for Shiprocket provider."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ShiprocketProduct(BaseModel):
    name: str
    sku: str
    units: int
    selling_price: float
    discount: Optional[float] = None
    tax: Optional[float] = None
    hsn: Optional[str] = None


class ShiprocketOrder(BaseModel):
    order_id: str
    order_date: str
    pickup_location: str
    billing_customer_name: str
    billing_last_name: Optional[str] = None
    billing_address: str
    billing_address_2: Optional[str] = None
    billing_city: str
    billing_pincode: str
    billing_state: str
    billing_country: str = "India"
    billing_email: Optional[str] = None
    billing_phone: str
    shipping_is_billing: bool = True
    order_items: List[ShiprocketProduct] = []
    payment_method: str = "Prepaid"
    sub_total: float
    length: float
    breadth: float
    height: float
    weight: float


class ShiprocketTrackingActivity(BaseModel):
    date: Optional[str] = None
    status: Optional[str] = None
    activity: Optional[str] = None
    location: Optional[str] = None
    sr_status: Optional[str] = None
    sr_status_label: Optional[str] = None


class ShiprocketTrackingResult(BaseModel):
    tracking_data: Optional[Dict[str, Any]] = None
    awb_code: Optional[str] = None
    courier_name: Optional[str] = None
    current_status: Optional[str] = None
    shipment_status: Optional[int] = None
    delivered_date: Optional[str] = None
    etd: Optional[str] = None
    activities: Optional[List[ShiprocketTrackingActivity]] = None
