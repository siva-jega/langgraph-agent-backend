"""Pydantic models for DTDC provider."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class DTDCPackage(BaseModel):
    name: str
    quantity: int
    weight: float
    declared_value: Optional[float] = None


class DTDCOrder(BaseModel):
    reference_number: str
    consignee_name: str
    consignee_phone: str
    consignee_address: str
    consignee_city: str
    consignee_state: str
    consignee_pincode: str
    origin_city: str
    origin_pincode: str
    product_type: str = "Non Document"
    actual_weight: float
    declared_value: float
    payment_mode: str = "Prepaid"
    num_pieces: int = 1


class DTDCTrackingActivity(BaseModel):
    date: Optional[str] = None
    status: Optional[str] = None
    activity: Optional[str] = None
    location: Optional[str] = None


class DTDCTrackingResult(BaseModel):
    consignment_number: Optional[str] = None
    status: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    delivered_date: Optional[str] = None
    scans: Optional[List[DTDCTrackingActivity]] = None
