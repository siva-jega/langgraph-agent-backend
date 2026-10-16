"""WareIQ tools: track orders, create shipments, cancel orders, check serviceability, get rates."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from common.tools.utils.writer import emit_update
from .utils import get_credentials_from_config, make_wareiq_request

logger = logging.getLogger(__name__)


def _error_response(tool_name: str, message: str) -> Tuple[str, Any]:
    return message, {
        "name": "WareIQ",
        "tool": tool_name,
        "data": {"success": False, "message": message},
    }


def _is_success(response: Dict[str, Any]) -> bool:
    return bool(
        response.get("success")
        or str(response.get("status", "")).lower() == "success"
        or response.get("status_code") in (200, 201)
    )


def _format_tracking(tracking_data: Dict[str, Any]) -> str:
    """Format WareIQ tracking data into a readable string."""
    lines = []

    awb = tracking_data.get("awb") or tracking_data.get("awb_number", "N/A")
    status = tracking_data.get("status") or tracking_data.get("current_status", "Unknown")
    courier = tracking_data.get("courier") or tracking_data.get("courier_name", "N/A")

    lines.extend([
        f"AWB: {awb}",
        f"Status: {status}",
        f"Courier: {courier}",
    ])

    origin = tracking_data.get("origin")
    if origin:
        lines.append(f"Origin: {origin}")

    destination = tracking_data.get("destination")
    if destination:
        lines.append(f"Destination: {destination}")

    edd = tracking_data.get("edd") or tracking_data.get("expected_delivery_date")
    if edd:
        lines.append(f"Expected Delivery: {edd}")

    delivered = tracking_data.get("delivered_date")
    if delivered:
        lines.append(f"Delivered: {delivered}")

    activities = tracking_data.get("activities") or tracking_data.get("scans", [])
    if activities:
        lines.append("\nRecent Activity:")
        for act in activities[:5]:
            date = act.get("date") or act.get("timestamp", "")
            activity = act.get("activity") or act.get("status", "")
            location = act.get("location", "")
            lines.append(f"  {date} — {activity} ({location})")

    return "\n".join(lines) if lines else "No tracking information available."


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(response_format="content_and_artifact")
async def track_order_wareiq(
    awb_code: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Track a shipment on WareIQ using its AWB (Air Waybill) code.
    Returns current status, courier info, delivery dates, and recent scan activity."""
    try:
        emit_update("Tracking order on WareIQ...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Track Order", err)

        resp = await make_wareiq_request(
            f"/tracking/v1/shipments/{awb_code}/all",
            token,
            method="GET",
        )

        tracking = resp.get("data") or resp.get("tracking_data", {})
        if not tracking:
            return _error_response("Track Order", f"No tracking data found for AWB {awb_code}.")

        msg = _format_tracking(tracking)
        return msg, {
            "name": "WareIQ",
            "tool": "Track Order",
            "data": {"success": True, "tracking": tracking},
        }
    except Exception as e:
        logger.error("Error tracking WareIQ order: %s", e)
        return _error_response("Track Order", f"Failed to track order: {e}")


@tool(response_format="content_and_artifact")
async def track_order_by_id_wareiq(
    order_id: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Track a shipment on WareIQ using its order ID.
    Returns current status, courier info, delivery dates, and recent scan activity."""
    try:
        emit_update("Tracking order on WareIQ...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Track Order by ID", err)

        resp = await make_wareiq_request(
            f"/orders/v2/{order_id}",
            token,
            method="POST",
        )

        tracking = resp.get("data") or resp.get("tracking_data", {})
        if not tracking:
            return _error_response("Track Order by ID", f"No tracking data for order {order_id}.")

        msg = _format_tracking(tracking)
        return msg, {
            "name": "WareIQ",
            "tool": "Track Order by ID",
            "data": {"success": True, "tracking": tracking},
        }
    except Exception as e:
        logger.error("Error tracking WareIQ order by ID: %s", e)
        return _error_response("Track Order by ID", f"Failed to track order: {e}")


@tool(response_format="content_and_artifact")
async def create_order_wareiq(
    order_id: str,
    customer_name: str,
    customer_phone: str,
    shipping_address: str,
    shipping_city: str,
    shipping_state: str,
    shipping_pincode: str,
    product_name: str,
    product_sku: str,
    product_quantity: int,
    product_price: float,
    sub_total: float,
    weight: float,
    config: RunnableConfig,
    order_date: Optional[str] = None,
    shipping_country: str = "India",
    customer_email: Optional[str] = None,
    payment_method: str = "prepaid",
    length: Optional[float] = None,
    breadth: Optional[float] = None,
    height: Optional[float] = None,
) -> Tuple[str, Any]:
    """Create a new order on WareIQ.

    Parameters:
    - order_id: Your unique order reference number
    - customer_name: Customer full name
    - customer_phone: Customer phone number
    - shipping_address, shipping_city, shipping_state, shipping_pincode: Delivery address
    - product_name, product_sku, product_quantity, product_price: Product info
    - sub_total: Total order value
    - weight: Weight in kg
    - payment_method: "Prepaid" (default) or "COD"
    - length, breadth, height: Optional dimensions in cm
    """
    try:
        emit_update("Creating order on WareIQ...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Create Order", err)

        order_payload: Dict[str, Any] = {
            "order_id": order_id,
            "order_date": order_date or datetime.now().strftime("%Y-%m-%d %H:%M"),
            "full_name": customer_name,
            "customer_phone": customer_phone,
            "address1": shipping_address,
            "city": shipping_city,
            "state": shipping_state,
            "pincode": shipping_pincode,
            "country": shipping_country,
            "products": [
                {
                    "name": product_name,
                    "sku": product_sku,
                    "quantity": product_quantity,
                    "price": str(product_price),
                    "amount": str(product_price * product_quantity),
                    "weight": str(weight),
                }
            ],
            "payment_method": payment_method.lower(),
            "total": str(sub_total),
            "weight": str(weight),
        }
        if customer_email:
            order_payload["customer_email"] = customer_email
        if length is not None:
            order_payload["products"][0]["length"] = str(length)
        if breadth is not None:
            order_payload["products"][0]["breadth"] = str(breadth)
        if height is not None:
            order_payload["products"][0]["height"] = str(height)

        resp = await make_wareiq_request(
            "/orders/v2/forward/create",
            token,
            method="POST",
            payload=order_payload,
        )

        data = resp.get("data", resp)
        wareiq_order_id = data.get("order_id") or data.get("id", "N/A")
        status = data.get("status", "")

        if _is_success(resp) or data.get("order_id") or data.get("unique_id"):
            msg = (
                f"Order created successfully!\n"
                f"WareIQ Order ID: {wareiq_order_id}\n"
                f"Status: {status or 'Created'}"
            )
            return msg, {
                "name": "WareIQ",
                "tool": "Create Order",
                "data": {
                    "success": True,
                    "order_id": wareiq_order_id,
                    "order_data": data,
                },
            }
        else:
            error_msg = data.get("message") or resp.get("message", "Unknown error")
            return _error_response("Create Order", f"Order creation failed: {error_msg}")

    except Exception as e:
        logger.error("Error creating WareIQ order: %s", e)
        return _error_response("Create Order", f"Failed to create order: {e}")


@tool(response_format="content_and_artifact")
async def cancel_order_wareiq(
    order_id: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Cancel an order on WareIQ by order ID.

    Parameters:
    - order_id: WareIQ order ID to cancel
    """
    try:
        emit_update("Cancelling order on WareIQ...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Cancel Order", err)

        resp = await make_wareiq_request(
            "/orders/v2/cancel",
            token,
            method="POST",
            payload={"orders": [{"unique_id": order_id}]},
        )

        data = resp.get("data", resp)
        if _is_success(resp):
            msg = f"Order {order_id} cancelled successfully."
            return msg, {
                "name": "WareIQ",
                "tool": "Cancel Order",
                "data": {"success": True, "result": data},
            }
        else:
            error_msg = data.get("message") or resp.get("message", "Cancellation failed")
            return _error_response("Cancel Order", f"Cancellation failed: {error_msg}")

    except Exception as e:
        logger.error("Error cancelling WareIQ order: %s", e)
        return _error_response("Cancel Order", f"Failed to cancel order: {e}")


@tool(response_format="content_and_artifact")
async def check_serviceability_wareiq(
    pincode: str,
    sku: str,
    config: RunnableConfig,
    quantity: int = 1,
) -> Tuple[str, Any]:
    """Check if a delivery route is serviceable on WareIQ and get available courier partners.

    Parameters:
    - pincode: Destination pincode
    - sku: WareIQ product SKU
    - quantity: Number of units (default: 1)
    """
    try:
        emit_update("Checking serviceability on WareIQ...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Check Serviceability", err)

        payload: Dict[str, Any] = {
            "pincode": pincode,
            "sku_list": [{"sku": sku, "quantity": quantity}],
        }

        resp = await make_wareiq_request(
            "/shipping/v1/pincode_serviceability",
            token,
            method="POST",
            payload=payload,
        )

        data = resp.get("data", {})
        couriers = data.get("available_couriers") or data.get("couriers", [])

        if not couriers:
            return _error_response(
                "Check Serviceability",
                f"SKU {sku} is not serviceable to {pincode}.",
            )

        lines = [f"Serviceability for SKU {sku} to {pincode}:"]
        for c in couriers[:10]:
            name = c.get("courier_name") or c.get("name", "Unknown")
            rate = c.get("rate") or c.get("total_charge", "N/A")
            etd = c.get("etd") or c.get("estimated_days", "N/A")
            cod_avail = "Yes" if c.get("cod") else "No"
            lines.append(f"  {name}: Rs.{rate} (ETD: {etd}, COD: {cod_avail})")

        if len(couriers) > 10:
            lines.append(f"  ... and {len(couriers) - 10} more couriers")

        msg = "\n".join(lines)
        return msg, {
            "name": "WareIQ",
            "tool": "Check Serviceability",
            "data": {"success": True, "couriers": couriers},
        }
    except Exception as e:
        logger.error("Error checking WareIQ serviceability: %s", e)
        return _error_response("Check Serviceability", f"Failed to check serviceability: {e}")


@tool(response_format="content_and_artifact")
async def get_order_details_wareiq(
    order_id: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Get details of an existing order on WareIQ by its order ID.
    Returns order info including items, dimensions, payment, and customer details."""
    try:
        emit_update("Fetching order details from WareIQ...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Order Details", err)

        resp = await make_wareiq_request(
            f"/orders/v2/{order_id}",
            token,
            method="POST",
        )

        data = resp.get("data", resp)
        if not data:
            return _error_response("Get Order Details", f"No details found for order {order_id}.")

        order_info = data if isinstance(data, dict) else {}
        lines = [
            f"Order ID: {order_info.get('order_id') or order_info.get('id', order_id)}",
            f"Status: {order_info.get('status', 'N/A')}",
            f"Payment Method: {order_info.get('payment_method', 'N/A')}",
            f"Total: Rs.{order_info.get('sub_total') or order_info.get('total', 'N/A')}",
            f"Customer: {order_info.get('customer_name', 'N/A')}",
            f"City: {order_info.get('shipping_city') or order_info.get('city', 'N/A')}",
            f"Pincode: {order_info.get('shipping_pincode') or order_info.get('pincode', 'N/A')}",
        ]

        awb = order_info.get("awb") or order_info.get("awb_number")
        if awb:
            lines.append(f"AWB: {awb}")

        courier = order_info.get("courier") or order_info.get("courier_name")
        if courier:
            lines.append(f"Courier: {courier}")

        weight = order_info.get("weight")
        if weight:
            lines.append(f"Weight: {weight} kg")

        msg = "\n".join(lines)
        return msg, {
            "name": "WareIQ",
            "tool": "Get Order Details",
            "data": {"success": True, "details": data},
        }
    except Exception as e:
        logger.error("Error getting WareIQ order details: %s", e)
        return _error_response("Get Order Details", f"Failed to get order details: {e}")


@tool(response_format="content_and_artifact")
async def get_shipping_rate_wareiq(
    order_id: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Get shipping rate estimates from WareIQ for available courier partners.

    Parameters:
    - order_id: Existing WareIQ order ID
    """
    try:
        emit_update("Fetching shipping rates from WareIQ...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Shipping Rate", err)

        payload: Dict[str, Any] = {"order_nos": [order_id]}

        resp = await make_wareiq_request(
            "/shipping/v1/couriers/estimates",
            token,
            method="POST",
            payload=payload,
        )

        data = resp.get("data", {})
        rates = data.get("rates") or data.get("available_couriers", [])

        if not rates:
            return _error_response("Get Shipping Rate", "No shipping rates available for this route.")

        lines = [f"Courier estimates for order {order_id}:"]
        for c in rates[:10]:
            name = c.get("courier_name") or c.get("name", "Unknown")
            rate = c.get("rate") or c.get("total_charge", "N/A")
            etd = c.get("etd") or c.get("estimated_days", "N/A")
            min_weight = c.get("min_weight", "N/A")
            lines.append(f"  {name}: Rs.{rate} (ETD: {etd}, Min Weight: {min_weight}kg)")

        if len(rates) > 10:
            lines.append(f"  ... and {len(rates) - 10} more options")

        msg = "\n".join(lines)
        return msg, {
            "name": "WareIQ",
            "tool": "Get Shipping Rate",
            "data": {"success": True, "rates": rates},
        }
    except Exception as e:
        logger.error("Error getting WareIQ shipping rate: %s", e)
        return _error_response("Get Shipping Rate", f"Failed to get shipping rate: {e}")


def get_wareiq_tools() -> List[BaseTool]:
    return [
        track_order_wareiq,
        track_order_by_id_wareiq,
        create_order_wareiq,
        cancel_order_wareiq,
        check_serviceability_wareiq,
        get_order_details_wareiq,
        get_shipping_rate_wareiq,
    ]
