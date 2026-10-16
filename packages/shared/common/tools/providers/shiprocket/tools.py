"""Shiprocket tools: track orders, create shipments, cancel orders, check serviceability, get rates."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from common.tools.utils.writer import emit_update
from .utils import get_credentials_from_config, make_shiprocket_request

logger = logging.getLogger(__name__)


def _error_response(tool_name: str, message: str) -> Tuple[str, Any]:
    return message, {
        "name": "Shiprocket",
        "tool": tool_name,
        "data": {"success": False, "message": message},
    }


def _format_tracking(tracking_data: Dict[str, Any]) -> str:
    """Format Shiprocket tracking data into a readable string."""
    shipment = tracking_data.get("shipment_track", [])
    track_activities = tracking_data.get("shipment_track_activities", [])

    if shipment and isinstance(shipment, list):
        info = shipment[0]
    elif isinstance(shipment, dict):
        info = shipment
    else:
        info = {}

    lines = []
    if info:
        lines.extend([
            f"AWB: {info.get('awb_code', 'N/A')}",
            f"Status: {info.get('current_status', 'Unknown')}",
            f"Courier: {info.get('courier_name', 'N/A')}",
            f"Origin: {info.get('origin', 'N/A')}",
            f"Destination: {info.get('destination', 'N/A')}",
        ])
        edd = info.get("edd")
        if edd:
            lines.append(f"Expected Delivery: {edd}")
        delivered = info.get("delivered_date")
        if delivered:
            lines.append(f"Delivered: {delivered}")

    if track_activities:
        lines.append("\nRecent Activity:")
        for act in track_activities[:5]:
            date = act.get("date", "")
            activity = act.get("activity", "")
            location = act.get("location", "")
            lines.append(f"  {date} — {activity} ({location})")

    return "\n".join(lines) if lines else "No tracking information available."


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(response_format="content_and_artifact")
async def track_order_shiprocket(
    awb_code: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Track a shipment on Shiprocket using its AWB (Air Waybill) code.
    Returns current status, courier info, delivery dates, and recent scan activity."""
    try:
        emit_update("Tracking order on Shiprocket...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Track Order", err)

        resp = await make_shiprocket_request(
            f"/courier/track/awb/{awb_code}",
            token,
            method="GET",
        )

        tracking = resp.get("tracking_data", {})
        if not tracking:
            return _error_response("Track Order", f"No tracking data found for AWB {awb_code}.")

        msg = _format_tracking(tracking)
        return msg, {
            "name": "Shiprocket",
            "tool": "Track Order",
            "data": {"success": True, "tracking": tracking},
        }
    except Exception as e:
        logger.error("Error tracking Shiprocket order: %s", e)
        return _error_response("Track Order", f"Failed to track order: {e}")


@tool(response_format="content_and_artifact")
async def track_order_by_id_shiprocket(
    shipment_id: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Track a shipment on Shiprocket using its Shiprocket shipment ID.
    Returns current status, courier info, delivery dates, and recent scan activity."""
    try:
        emit_update("Tracking shipment on Shiprocket...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Track Shipment", err)

        resp = await make_shiprocket_request(
            f"/courier/track/shipment/{shipment_id}",
            token,
            method="GET",
        )

        tracking = resp.get("tracking_data", {})
        if not tracking:
            return _error_response("Track Shipment", f"No tracking data for shipment {shipment_id}.")

        msg = _format_tracking(tracking)
        return msg, {
            "name": "Shiprocket",
            "tool": "Track Shipment",
            "data": {"success": True, "tracking": tracking},
        }
    except Exception as e:
        logger.error("Error tracking Shiprocket shipment: %s", e)
        return _error_response("Track Shipment", f"Failed to track shipment: {e}")


@tool(response_format="content_and_artifact")
async def create_order_shiprocket(
    order_id: str,
    order_date: str,
    pickup_location: str,
    billing_customer_name: str,
    billing_address: str,
    billing_city: str,
    billing_pincode: str,
    billing_state: str,
    billing_phone: str,
    product_name: str,
    product_sku: str,
    product_quantity: int,
    product_price: float,
    sub_total: float,
    weight: float,
    length: float,
    breadth: float,
    height: float,
    config: RunnableConfig,
    billing_country: str = "India",
    billing_last_name: Optional[str] = None,
    billing_email: Optional[str] = None,
    billing_address_2: Optional[str] = None,
    payment_method: str = "Prepaid",
    shipping_is_billing: bool = True,
) -> Tuple[str, Any]:
    """Create a new order on Shiprocket.

    Parameters:
    - order_id: Your unique order reference number
    - order_date: Date in YYYY-MM-DD HH:MM format
    - pickup_location: Pickup location name as saved in Shiprocket
    - billing_customer_name, billing_address, billing_city, billing_pincode, billing_state, billing_phone: Customer details
    - product_name, product_sku, product_quantity, product_price: Product info
    - sub_total: Total order value
    - weight: Weight in kg
    - length, breadth, height: Dimensions in cm
    - payment_method: "Prepaid" (default) or "COD"
    """
    try:
        emit_update("Creating order on Shiprocket...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Create Order", err)

        order_payload: Dict[str, Any] = {
            "order_id": order_id,
            "order_date": order_date or datetime.now().strftime("%Y-%m-%d %H:%M"),
            "pickup_location": pickup_location,
            "billing_customer_name": billing_customer_name,
            "billing_address": billing_address,
            "billing_city": billing_city,
            "billing_pincode": billing_pincode,
            "billing_state": billing_state,
            "billing_country": billing_country,
            "billing_phone": billing_phone,
            "shipping_is_billing": shipping_is_billing,
            "order_items": [
                {
                    "name": product_name,
                    "sku": product_sku,
                    "units": product_quantity,
                    "selling_price": str(product_price),
                }
            ],
            "payment_method": payment_method,
            "sub_total": sub_total,
            "length": length,
            "breadth": breadth,
            "height": height,
            "weight": weight,
        }
        if billing_last_name:
            order_payload["billing_last_name"] = billing_last_name
        if billing_email:
            order_payload["billing_email"] = billing_email
        if billing_address_2:
            order_payload["billing_address_2"] = billing_address_2

        resp = await make_shiprocket_request(
            "/orders/create/adhoc",
            token,
            method="POST",
            payload=order_payload,
        )

        sr_order_id = resp.get("order_id", "N/A")
        shipment_id = resp.get("shipment_id", "N/A")
        status = resp.get("status", "")
        status_code = resp.get("status_code", 0)

        if status_code == 1 or status == "NEW":
            msg = (
                f"Order created successfully!\n"
                f"Shiprocket Order ID: {sr_order_id}\n"
                f"Shipment ID: {shipment_id}\n"
                f"Status: {status}"
            )
            return msg, {
                "name": "Shiprocket",
                "tool": "Create Order",
                "data": {
                    "success": True,
                    "order_id": sr_order_id,
                    "shipment_id": shipment_id,
                    "order_data": resp,
                },
            }
        else:
            error_msg = resp.get("message", "Unknown error")
            return _error_response("Create Order", f"Order creation failed: {error_msg}")

    except Exception as e:
        logger.error("Error creating Shiprocket order: %s", e)
        return _error_response("Create Order", f"Failed to create order: {e}")


@tool(response_format="content_and_artifact")
async def cancel_order_shiprocket(
    order_ids: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Cancel one or more orders on Shiprocket by order ID(s).

    Parameters:
    - order_ids: Comma-separated Shiprocket order IDs to cancel. Example: "1234,5678"
    """
    try:
        emit_update("Cancelling order on Shiprocket...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Cancel Order", err)

        ids = [int(oid.strip()) for oid in order_ids.split(",") if oid.strip()]
        resp = await make_shiprocket_request(
            "/orders/cancel",
            token,
            method="POST",
            payload={"ids": ids},
        )

        if resp.get("status") == 200 or resp.get("message"):
            msg = f"Cancellation result: {resp.get('message', 'Orders cancelled successfully')}"
            return msg, {
                "name": "Shiprocket",
                "tool": "Cancel Order",
                "data": {"success": True, "result": resp},
            }
        else:
            return _error_response("Cancel Order", f"Cancellation failed: {resp}")

    except Exception as e:
        logger.error("Error cancelling Shiprocket order: %s", e)
        return _error_response("Cancel Order", f"Failed to cancel order: {e}")


@tool(response_format="content_and_artifact")
async def check_serviceability_shiprocket(
    pickup_postcode: str,
    delivery_postcode: str,
    weight: float,
    config: RunnableConfig,
    cod: bool = False,
) -> Tuple[str, Any]:
    """Check if a delivery route is serviceable on Shiprocket and get available courier partners.

    Parameters:
    - pickup_postcode: Origin/warehouse pincode
    - delivery_postcode: Destination pincode
    - weight: Package weight in kg
    - cod: Whether the order is Cash on Delivery (default: false)
    """
    try:
        emit_update("Checking serviceability on Shiprocket...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Check Serviceability", err)

        params: Dict[str, Any] = {
            "pickup_postcode": pickup_postcode,
            "delivery_postcode": delivery_postcode,
            "weight": weight,
            "cod": 1 if cod else 0,
        }

        resp = await make_shiprocket_request(
            "/courier/serviceability/",
            token,
            method="GET",
            params=params,
        )

        data = resp.get("data", {})
        couriers = data.get("available_courier_companies", [])

        if not couriers:
            return _error_response(
                "Check Serviceability",
                f"No courier partners available for {pickup_postcode} → {delivery_postcode}.",
            )

        lines = [f"Serviceability ({pickup_postcode} → {delivery_postcode}, {weight}kg):"]
        for c in couriers[:10]:
            name = c.get("courier_name", "Unknown")
            rate = c.get("rate", "N/A")
            etd = c.get("etd", "N/A")
            cod_avail = "Yes" if c.get("cod") else "No"
            lines.append(f"  {name}: Rs.{rate} (ETD: {etd}, COD: {cod_avail})")

        if len(couriers) > 10:
            lines.append(f"  ... and {len(couriers) - 10} more couriers")

        msg = "\n".join(lines)
        return msg, {
            "name": "Shiprocket",
            "tool": "Check Serviceability",
            "data": {"success": True, "couriers": couriers},
        }
    except Exception as e:
        logger.error("Error checking Shiprocket serviceability: %s", e)
        return _error_response("Check Serviceability", f"Failed to check serviceability: {e}")


@tool(response_format="content_and_artifact")
async def get_order_details_shiprocket(
    order_id: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Get details of an existing order on Shiprocket by its order ID.
    Returns order info including items, dimensions, payment, and customer details."""
    try:
        emit_update("Fetching order details from Shiprocket...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Order Details", err)

        resp = await make_shiprocket_request(
            f"/orders/show/{order_id}",
            token,
            method="GET",
        )

        data = resp.get("data", resp)
        if not data:
            return _error_response("Get Order Details", f"No details found for order {order_id}.")

        order_info = data if isinstance(data, dict) else {}
        lines = [
            f"Order ID: {order_info.get('id', order_id)}",
            f"Channel Order ID: {order_info.get('channel_order_id', 'N/A')}",
            f"Status: {order_info.get('status', 'N/A')}",
            f"Payment Method: {order_info.get('payment_method', 'N/A')}",
            f"Total: Rs.{order_info.get('total', 'N/A')}",
            f"Customer: {order_info.get('customer_name', 'N/A')}",
            f"City: {order_info.get('customer_city', 'N/A')}",
            f"Pincode: {order_info.get('customer_pincode', 'N/A')}",
        ]

        shipments = order_info.get("shipments", [])
        if shipments:
            s = shipments[0] if isinstance(shipments, list) else shipments
            if isinstance(s, dict):
                lines.extend([
                    f"AWB: {s.get('awb_code', 'N/A')}",
                    f"Courier: {s.get('courier_name', 'N/A')}",
                    f"Weight: {s.get('weight', 'N/A')} kg",
                ])

        msg = "\n".join(lines)
        return msg, {
            "name": "Shiprocket",
            "tool": "Get Order Details",
            "data": {"success": True, "details": data},
        }
    except Exception as e:
        logger.error("Error getting Shiprocket order details: %s", e)
        return _error_response("Get Order Details", f"Failed to get order details: {e}")


@tool(response_format="content_and_artifact")
async def get_shipping_rate_shiprocket(
    pickup_postcode: str,
    delivery_postcode: str,
    weight: float,
    config: RunnableConfig,
    cod: bool = False,
    length: Optional[float] = None,
    breadth: Optional[float] = None,
    height: Optional[float] = None,
) -> Tuple[str, Any]:
    """Get shipping rate estimates from Shiprocket for available courier partners.

    Parameters:
    - pickup_postcode: Sender/warehouse pincode
    - delivery_postcode: Recipient pincode
    - weight: Package weight in kg
    - cod: Whether the order is Cash on Delivery (default: false)
    - length, breadth, height: Optional package dimensions in cm
    """
    try:
        emit_update("Fetching shipping rates from Shiprocket...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Shipping Rate", err)

        params: Dict[str, Any] = {
            "pickup_postcode": pickup_postcode,
            "delivery_postcode": delivery_postcode,
            "weight": weight,
            "cod": 1 if cod else 0,
        }
        if length is not None:
            params["length"] = length
        if breadth is not None:
            params["breadth"] = breadth
        if height is not None:
            params["height"] = height

        resp = await make_shiprocket_request(
            "/courier/serviceability/",
            token,
            method="GET",
            params=params,
        )

        data = resp.get("data", {})
        couriers = data.get("available_courier_companies", [])

        if not couriers:
            return _error_response("Get Shipping Rate", "No shipping rates available for this route.")

        lines = [f"Shipping rates ({pickup_postcode} → {delivery_postcode}, {weight}kg):"]
        for c in couriers[:10]:
            name = c.get("courier_name", "Unknown")
            rate = c.get("rate", "N/A")
            etd = c.get("etd", "N/A")
            min_weight = c.get("min_weight", "N/A")
            lines.append(f"  {name}: Rs.{rate} (ETD: {etd}, Min Weight: {min_weight}kg)")

        if len(couriers) > 10:
            lines.append(f"  ... and {len(couriers) - 10} more options")

        msg = "\n".join(lines)
        return msg, {
            "name": "Shiprocket",
            "tool": "Get Shipping Rate",
            "data": {"success": True, "rates": couriers},
        }
    except Exception as e:
        logger.error("Error getting Shiprocket shipping rate: %s", e)
        return _error_response("Get Shipping Rate", f"Failed to get shipping rate: {e}")


def get_shiprocket_tools() -> List[BaseTool]:
    return [
        track_order_shiprocket,
        track_order_by_id_shiprocket,
        create_order_shiprocket,
        cancel_order_shiprocket,
        check_serviceability_shiprocket,
        get_order_details_shiprocket,
        get_shipping_rate_shiprocket,
    ]
