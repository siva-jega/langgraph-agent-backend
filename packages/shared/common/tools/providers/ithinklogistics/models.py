"""Pydantic models for iThink Logistics provider."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class IThinkProduct(BaseModel):
    product_name: str
    product_quantity: int
    product_price: float
    product_sku: Optional[str] = None
    product_hsn_code: Optional[str] = None
    product_tax_rate: Optional[float] = None
    product_discount: Optional[float] = None


class IThinkShipment(BaseModel):
    order: str
    order_date: str
    name: str
    add: str
    pin: int
    phone: int
    products: List[IThinkProduct]
    shipment_length: float
    shipment_width: float
    shipment_height: float
    weight: float
    total_amount: float
    return_address_id: int
    order_type: str = "forward"
    payment_mode: str = "cod"
    sub_order: Optional[str] = None
    company_name: Optional[str] = None
    add2: Optional[str] = None
    add3: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    alt_phone: Optional[int] = None
    email: Optional[str] = None
    cod_amount: Optional[float] = None
    shipping_charges: Optional[float] = None
    giftwrap_charges: Optional[float] = None
    total_discount: Optional[float] = None
    eway_bill_number: Optional[str] = None
    gst_number: Optional[str] = None


class IThinkScanDetail(BaseModel):
    status: Optional[str] = None
    status_code: Optional[str] = None
    scan_location: Optional[str] = None
    remark: Optional[str] = None
    scan_date_time: Optional[str] = None
    status_reason: Optional[str] = None


class IThinkTrackingResult(BaseModel):
    awb_no: Optional[str] = None
    logistic: Optional[str] = None
    current_status: Optional[str] = None
    current_status_code: Optional[str] = None
    order_type: Optional[str] = None
    cancel_status: Optional[str] = None
    scan_details: Optional[List[IThinkScanDetail]] = None
    order_details: Optional[Dict[str, Any]] = None
    customer_details: Optional[Dict[str, Any]] = None
    order_date_time: Optional[Dict[str, Any]] = None
    expected_delivery_date: Optional[str] = None
