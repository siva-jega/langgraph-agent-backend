"""Delhivery tools: track orders, create shipments, cancel orders, check serviceability, get rates."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from common.tools.utils.writer import emit_update
from .utils import get_credentials_from_config, make_delhivery_request

logger = logging.getLogger(__name__)


def _error_response(tool_name: str, message: str) -> Tuple[str, Any]:
    return message, {
        "name": "Delhivery",
        "tool": tool_name,
        "data": {"success": False, "message": message},
    }


def _format_tracking(pkg: Dict[str, Any]) -> str:
    """Format Delhivery tracking data into a readable string."""
    status = pkg.get("Status", {})
    lines = [
        f"Waybill: {pkg.get('Waybill', 'N/A')}",
        f"Status: {status.get('Status', 'Unknown')}",
        f"Instructions: {status.get('Instructions', 'N/A')}",
        f"Origin: {pkg.get('Origin', 'N/A')}",
        f"Destination: {pkg.get('Destination', 'N/A')}",
    ]

    edd = pkg.get("ExpectedDeliveryDate") or pkg.get("PromisedDeliveryDate")
    if edd:
        lines.append(f"Expected Delivery: {edd}")

    scans = pkg.get("Scans", [])
    if scans:
        lines.append("\nRecent Activity:")
        for scan in scans[:5]:
            scan_detail = scan.get("ScanDetail", {})
            date = scan_detail.get("ScanDateTime", "")
            activity = scan_detail.get("Instructions", "")
            location = scan_detail.get("ScannedLocation", "")
            lines.append(f"  {date} — {activity} ({location})")

    return "\n".join(lines) if lines else "No tracking information available."


@tool(response_format="content_and_artifact")
async def track_order_delhivery(
    waybill: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Track a shipment on Delhivery using its waybill (AWB) number.
    Returns current status, delivery dates, and recent scan activity."""
    try:
        emit_update("Tracking order on Delhivery...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Track Order", err)

        resp = await make_delhivery_request(
            "/api/v1/packages/json/",
            token,
            method="GET",
            params={"waybill": waybill},
        )

        results = resp.get("ShipmentData", [])
        if not results:
            return _error_response("Track Order", f"No tracking data found for waybill {waybill}.")

        pkg = results[0].get("Shipment", {})
        msg = _format_tracking(pkg)
        return msg, {
            "name": "Delhivery",
            "tool": "Track Order",
            "data": {"success": True, "tracking": pkg},
        }
    except Exception as e:
        logger.error("Error tracking Delhivery order: %s", e)
        return _error_response("Track Order", f"Failed to track order: {e}")


@tool(response_format="content_and_artifact")
async def create_order_delhivery(
    order_id: str,
    name: str,
    phone: str,
    address: str,
    city: str,
    state: str,
    pin: str,
    product_name: str,
    product_quantity: int,
    weight: float,
    total_amount: float,
    config: RunnableConfig,
    country: str = "India",
    payment_mode: str = "Prepaid",
) -> Tuple[str, Any]:
    """Create a new shipment order on Delhivery.

    Parameters:
    - order_id: Your unique order reference number
    - name: Recipient name
    - phone: Recipient phone number
    - address, city, state, pin: Delivery address details
    - product_name: Name of the product
    - product_quantity: Number of items
    - weight: Weight in grams
    - total_amount: Order value
    - payment_mode: "Prepaid" (default) or "COD"
    """
    try:
        emit_update("Creating order on Delhivery...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Create Order", err)

        shipment_data = {
            "shipments": [
                {
                    "order": order_id,
                    "name": name,
                    "phone": phone,
                    "add": address,
                    "city": city,
                    "state": state,
                    "pin": pin,
                    "country": country,
                    "payment_mode": payment_mode,
                    "products_desc": product_name,
                    "quantity": product_quantity,
                    "gm": weight,
                    "order_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "total_amount": total_amount,
                }
            ],
            "pickup_location": {"name": "default"},
        }

        resp = await make_delhivery_request(
            "/api/cmu/create.json",
            token,
            method="POST",
            payload=shipment_data,
        )

        success = resp.get("success", False)
        packages = resp.get("packages", [])

        if success or packages:
            waybill = packages[0].get("waybill", "N/A") if packages else "N/A"
            msg = (
                f"Order created successfully!\n"
                f"Waybill: {waybill}\n"
                f"Order ID: {order_id}\n"
                f"Status: Created"
            )
            return msg, {
                "name": "Delhivery",
                "tool": "Create Order",
                "data": {"success": True, "waybill": waybill, "order_data": resp},
            }
        else:
            error_msg = resp.get("rmk", resp.get("message", "Unknown error"))
            return _error_response("Create Order", f"Order creation failed: {error_msg}")

    except Exception as e:
        logger.error("Error creating Delhivery order: %s", e)
        return _error_response("Create Order", f"Failed to create order: {e}")


@tool(response_format="content_and_artifact")
async def cancel_order_delhivery(
    waybill: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Cancel a shipment on Delhivery by its waybill number.

    Parameters:
    - waybill: Delhivery waybill (AWB) number to cancel
    """
    try:
        emit_update("Cancelling order on Delhivery...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Cancel Order", err)

        resp = await make_delhivery_request(
            "/api/p/edit",
            token,
            method="POST",
            payload={"waybill": waybill, "cancellation": "true"},
        )

        if resp.get("status"):
            msg = f"Cancellation result: Waybill {waybill} cancelled successfully."
            return msg, {
                "name": "Delhivery",
                "tool": "Cancel Order",
                "data": {"success": True, "result": resp},
            }
        else:
            error_msg = resp.get("rmk", resp.get("message", "Cancellation failed"))
            return _error_response("Cancel Order", f"Cancellation failed: {error_msg}")

    except Exception as e:
        logger.error("Error cancelling Delhivery order: %s", e)
        return _error_response("Cancel Order", f"Failed to cancel order: {e}")


@tool(response_format="content_and_artifact")
async def check_serviceability_delhivery(
    pincode: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Check if a pincode is serviceable by Delhivery.

    Parameters:
    - pincode: Indian pincode to check
    """
    try:
        emit_update("Checking serviceability on Delhivery...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Check Serviceability", err)

        resp = await make_delhivery_request(
            "/c/api/pin-codes/json/",
            token,
            method="GET",
            params={"filter_codes": pincode},
        )

        delivery_codes = resp.get("delivery_codes", [])
        if not delivery_codes:
            return _error_response(
                "Check Serviceability",
                f"Pincode {pincode} is not serviceable by Delhivery.",
            )

        info = delivery_codes[0].get("postal_code", {})
        lines = [
            f"Pincode {pincode} is serviceable:",
            f"  District: {info.get('district', 'N/A')}",
            f"  State: {info.get('state_code', 'N/A')}",
            f"  COD Available: {'Yes' if info.get('cod') == 'Y' else 'No'}",
            f"  Prepaid Available: {'Yes' if info.get('pre_paid') == 'Y' else 'No'}",
            f"  Pickup Available: {'Yes' if info.get('pickup') == 'Y' else 'No'}",
            f"  Repl Available: {'Yes' if info.get('repl') == 'Y' else 'No'}",
        ]

        msg = "\n".join(lines)
        return msg, {
            "name": "Delhivery",
            "tool": "Check Serviceability",
            "data": {"success": True, "pincode_info": info},
        }
    except Exception as e:
        logger.error("Error checking Delhivery serviceability: %s", e)
        return _error_response("Check Serviceability", f"Failed to check serviceability: {e}")


@tool(response_format="content_and_artifact")
async def get_shipping_rate_delhivery(
    origin_pin: str,
    destination_pin: str,
    weight: float,
    config: RunnableConfig,
    cod: bool = False,
    declared_value: Optional[float] = None,
) -> Tuple[str, Any]:
    """Get shipping rate estimate from Delhivery for a given route and weight.

    Parameters:
    - origin_pin: Origin/pickup pincode
    - destination_pin: Destination pincode
    - weight: Package weight in grams
    - cod: Whether the order is Cash on Delivery (default: false)
    - declared_value: Optional declared value of the shipment
    """
    try:
        emit_update("Fetching shipping rates from Delhivery...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Shipping Rate", err)

        params: Dict[str, Any] = {
            "md": "E",
            "ss": "Delivered",
            "d_pin": destination_pin,
            "o_pin": origin_pin,
            "cgm": weight,
            "pt": "COD" if cod else "Pre-paid",
            "cod": "1" if cod else "0",
        }
        if declared_value is not None:
            params["dv"] = declared_value

        resp = await make_delhivery_request(
            "/api/kinko/v1/invoice/charges/.json",
            token,
            method="GET",
            params=params,
        )

        if not resp:
            return _error_response("Get Shipping Rate", "No shipping rates available for this route.")

        total = resp.get("total_amount", "N/A")
        charge_breakdown = resp.get("charge_breakdown", {})

        lines = [
            f"Shipping rate ({origin_pin} → {destination_pin}, {weight}g):",
            f"  Total: Rs.{total}",
        ]
        for key, val in charge_breakdown.items():
            lines.append(f"  {key}: Rs.{val}")

        msg = "\n".join(lines)
        return msg, {
            "name": "Delhivery",
            "tool": "Get Shipping Rate",
            "data": {"success": True, "rates": resp},
        }
    except Exception as e:
        logger.error("Error getting Delhivery shipping rate: %s", e)
        return _error_response("Get Shipping Rate", f"Failed to get shipping rate: {e}")


@tool(response_format="content_and_artifact")
async def get_order_details_delhivery(
    waybill: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Get details of an existing shipment on Delhivery by its waybill number.
    Returns shipment info including status, origin, destination, and scan history."""
    try:
        emit_update("Fetching order details from Delhivery...")
        token, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Order Details", err)

        resp = await make_delhivery_request(
            "/api/v1/packages/json/",
            token,
            method="GET",
            params={"waybill": waybill, "verbose": "2"},
        )

        results = resp.get("ShipmentData", [])
        if not results:
            return _error_response("Get Order Details", f"No details found for waybill {waybill}.")

        pkg = results[0].get("Shipment", {})
        msg = _format_tracking(pkg)
        return msg, {
            "name": "Delhivery",
            "tool": "Get Order Details",
            "data": {"success": True, "details": pkg},
        }
    except Exception as e:
        logger.error("Error getting Delhivery order details: %s", e)
        return _error_response("Get Order Details", f"Failed to get order details: {e}")


def get_delhivery_tools() -> List[BaseTool]:
    return [
        track_order_delhivery,
        create_order_delhivery,
        cancel_order_delhivery,
        check_serviceability_delhivery,
        get_order_details_delhivery,
        get_shipping_rate_delhivery,
    ]
