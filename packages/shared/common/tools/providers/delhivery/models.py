"""Pydantic models for Delhivery provider."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class DelhiveryPackage(BaseModel):
    name: str
    quantity: int
    weight: float
    selling_price: Optional[float] = None
    sku: Optional[str] = None


class DelhiveryOrder(BaseModel):
    order_id: str
    name: str
    phone: str
    address: str
    city: str
    state: str
    pin: str
    country: str = "India"
    payment_mode: str = "Prepaid"
    products: List[DelhiveryPackage] = []
    total_amount: float
    weight: float


class DelhiveryTrackingActivity(BaseModel):
    date: Optional[str] = None
    status: Optional[str] = None
    activity: Optional[str] = None
    location: Optional[str] = None


class DelhiveryTrackingResult(BaseModel):
    waybill: Optional[str] = None
    status: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    expected_date: Optional[str] = None
    scans: Optional[List[DelhiveryTrackingActivity]] = None
