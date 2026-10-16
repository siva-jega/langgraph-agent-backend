"""iThink Logistics tools: track orders, create shipments, cancel orders, check pincode, get rates."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from common.tools.utils.writer import emit_update
from .utils import (
    get_credentials_from_config,
    make_ithinklogistics_request,
    BASE_URL_TRACKING,
    BASE_URL_ORDERS,
)

logger = logging.getLogger(__name__)


def _error_response(tool_name: str, message: str) -> Tuple[str, Any]:
    return message, {
        "name": "iThink Logistics",
        "tool": tool_name,
        "data": {"success": False, "message": message},
    }


def _format_tracking(awb: str, info: Dict[str, Any]) -> str:
    """Format tracking info into a readable string."""
    status = info.get("current_status", "Unknown")
    logistic = info.get("logistic", "N/A")
    order_type = info.get("order_type", "N/A")
    cancel_status = info.get("cancel_status", "")

    last_scan = info.get("last_scan_details", {})
    last_location = last_scan.get("scan_location", "N/A") if last_scan else "N/A"
    last_time = last_scan.get("status_date_time", "N/A") if last_scan else "N/A"
    last_remark = last_scan.get("remark", "") if last_scan else ""

    dates = info.get("order_date_time", {})
    pickup_date = dates.get("pickup_date", "N/A") if dates else "N/A"
    delivery_date = dates.get("delivery_date", "N/A") if dates else "N/A"

    customer = info.get("customer_details", {})
    customer_name = customer.get("customer_name", "N/A") if customer else "N/A"
    customer_city = customer.get("customer_city", "N/A") if customer else "N/A"

    lines = [
        f"AWB: {awb}",
        f"Status: {status}",
        f"Logistics Partner: {logistic}",
        f"Order Type: {order_type}",
        f"Last Scan: {last_location} at {last_time}",
    ]
    if last_remark:
        lines.append(f"Remark: {last_remark}")
    lines.extend([
        f"Pickup Date: {pickup_date}",
        f"Delivery Date: {delivery_date}",
        f"Customer: {customer_name}, {customer_city}",
    ])
    if cancel_status and cancel_status != "Pending":
        lines.append(f"Cancel Status: {cancel_status}")

    expected = info.get("expected_delivery_date")
    if expected:
        lines.append(f"Expected Delivery: {expected}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(response_format="content_and_artifact")
async def track_order_ithinklogistics(
    awb_number: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Track a domestic shipment on iThink Logistics using its AWB (Air Waybill) number.
    Returns current status, scan history, customer details, and delivery dates."""
    try:
        emit_update("Tracking order on iThink Logistics...")
        access_token, secret_key, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Track Order", err)

        payload = {"data": {"awb_number_list": awb_number}}
        resp = await make_ithinklogistics_request(
            "/api_v3/order/track.json",
            payload,
            access_token,
            secret_key,
            base_url=BASE_URL_TRACKING,
        )

        data = resp.get("data", {})
        if not data:
            return _error_response("Track Order", f"No tracking data found for AWB {awb_number}.")

        awb_data = data.get(awb_number, {})
        if not awb_data or awb_data.get("message") == "fail":
            return _error_response("Track Order", f"No tracking info for AWB {awb_number}. Check the AWB number.")

        msg = _format_tracking(awb_number, awb_data)
        return msg, {
            "name": "iThink Logistics",
            "tool": "Track Order",
            "data": {"success": True, "tracking": awb_data},
        }
    except Exception as e:
        logger.error("Error tracking iThink order: %s", e)
        return _error_response("Track Order", f"Failed to track order: {e}")


@tool(response_format="content_and_artifact")
async def track_international_order_ithinklogistics(
    awb_number: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Track an international shipment on iThink Logistics using its AWB number.
    Returns current status, expected delivery date, scan history, and customer details."""
    try:
        emit_update("Tracking international order on iThink Logistics...")
        access_token, secret_key, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Track International Order", err)

        payload = {"data": {"awb_number_list": awb_number}}
        resp = await make_ithinklogistics_request(
            "/api_v3/order_intl/track.json",
            payload,
            access_token,
            secret_key,
            base_url=BASE_URL_TRACKING,
        )

        data = resp.get("data", {})
        if not data:
            return _error_response("Track International Order", f"No tracking data found for AWB {awb_number}.")

        awb_data = data.get(awb_number, {})
        if not awb_data or awb_data.get("message") == "fail":
            return _error_response("Track International Order", f"No tracking info for AWB {awb_number}.")

        msg = _format_tracking(awb_number, awb_data)
        return msg, {
            "name": "iThink Logistics",
            "tool": "Track International Order",
            "data": {"success": True, "tracking": awb_data},
        }
    except Exception as e:
        logger.error("Error tracking iThink international order: %s", e)
        return _error_response("Track International Order", f"Failed to track order: {e}")


@tool(response_format="content_and_artifact")
async def create_order_ithinklogistics(
    order_id: str,
    customer_name: str,
    customer_address: str,
    customer_pincode: int,
    customer_phone: int,
    product_name: str,
    product_quantity: int,
    product_price: float,
    total_amount: float,
    weight: float,
    shipment_length: float,
    shipment_width: float,
    shipment_height: float,
    pickup_address_id: int,
    return_address_id: int,
    logistics: str,
    config: RunnableConfig,
    order_type: str = "forward",
    payment_mode: str = "cod",
    customer_email: Optional[str] = None,
    customer_city: Optional[str] = None,
    customer_state: Optional[str] = None,
    cod_amount: Optional[float] = None,
    s_type: Optional[str] = None,
) -> Tuple[str, Any]:
    """Create a new shipment order on iThink Logistics.

    Parameters:
    - order_id: Your unique order reference number
    - customer_name, customer_address, customer_pincode, customer_phone: Recipient details
    - product_name, product_quantity, product_price: Product info
    - total_amount: Total order value
    - weight: Weight in kg
    - shipment_length, shipment_width, shipment_height: Dimensions in cm
    - pickup_address_id: Warehouse/pickup address ID from iThink dashboard
    - return_address_id: Return address ID from iThink dashboard
    - logistics: Carrier name — "delhivery", "bluedart", "xpressbees", "ecom", "ekart"
    - order_type: "forward" (default) or "reverse"
    - payment_mode: "cod" (default) or "Prepaid"
    - s_type: Optional — "air" or "surface" (for bluedart/delhivery)
    """
    try:
        emit_update("Creating order on iThink Logistics...")
        access_token, secret_key, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Create Order", err)

        shipment = {
            "order": order_id,
            "order_date": "",
            "order_type": order_type,
            "payment_mode": payment_mode,
            "name": customer_name,
            "add": customer_address,
            "pin": customer_pincode,
            "phone": customer_phone,
            "products": [
                {
                    "product_name": product_name,
                    "product_quantity": product_quantity,
                    "product_price": product_price,
                }
            ],
            "shipment_length": shipment_length,
            "shipment_width": shipment_width,
            "shipment_height": shipment_height,
            "weight": weight,
            "total_amount": total_amount,
            "return_address_id": return_address_id,
        }
        if customer_email:
            shipment["email"] = customer_email
        if customer_city:
            shipment["city"] = customer_city
        if customer_state:
            shipment["state"] = customer_state
        if cod_amount is not None:
            shipment["cod_amount"] = cod_amount

        payload: Dict[str, Any] = {
            "data": {
                "shipments": [shipment],
                "pickup_address_id": pickup_address_id,
                "logistics": logistics,
            }
        }
        if s_type:
            payload["data"]["s_type"] = s_type

        # Use order_date from current time
        from datetime import datetime
        shipment["order_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        resp = await make_ithinklogistics_request(
            "/api_v3/order/add.json",
            payload,
            access_token,
            secret_key,
            base_url=BASE_URL_ORDERS,
        )

        status = resp.get("status", "")
        resp_data = resp.get("data", {})

        if status == "success" or resp.get("status_code") == 200:
            # Response data is keyed by index "1", "2", etc.
            first_key = next(iter(resp_data), None)
            order_result = resp_data.get(first_key, {}) if first_key else {}
            waybill = order_result.get("waybill", "N/A")
            tracking_url = order_result.get("tracking_url", "")
            logistic_name = order_result.get("logistic_name", logistics)
            remark = order_result.get("remark", "")

            msg = f"Order created successfully!\nAWB: {waybill}\nLogistics: {logistic_name}"
            if tracking_url:
                msg += f"\nTracking URL: {tracking_url}"
            if remark:
                msg += f"\nRemark: {remark}"

            return msg, {
                "name": "iThink Logistics",
                "tool": "Create Order",
                "data": {
                    "success": True,
                    "waybill": waybill,
                    "tracking_url": tracking_url,
                    "logistic_name": logistic_name,
                    "order_data": order_result,
                },
            }
        else:
            html_message = resp.get("html_message", "Unknown error")
            return _error_response("Create Order", f"Order creation failed: {html_message}")

    except Exception as e:
        logger.error("Error creating iThink order: %s", e)
        return _error_response("Create Order", f"Failed to create order: {e}")


@tool(response_format="content_and_artifact")
async def cancel_order_ithinklogistics(
    awb_numbers: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Cancel one or more orders on iThink Logistics by AWB number(s).

    Parameters:
    - awb_numbers: Comma-separated AWB numbers to cancel (max 100). Example: "86210010463,86210010464"
    """
    try:
        emit_update("Cancelling order on iThink Logistics...")
        access_token, secret_key, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Cancel Order", err)

        payload = {"data": {"awb_numbers": awb_numbers}}
        resp = await make_ithinklogistics_request(
            "/api_v3/order/cancel.json",
            payload,
            access_token,
            secret_key,
            base_url=BASE_URL_ORDERS,
        )

        status = resp.get("status", "")
        resp_data = resp.get("data", {})

        if status == "success" or resp.get("status_code") == 200:
            results = []
            for key, val in resp_data.items():
                awb_status = val.get("status", "Unknown")
                remark = val.get("remark", "")
                results.append(f"AWB {key}: {awb_status}" + (f" — {remark}" if remark else ""))

            msg = "Cancellation results:\n" + "\n".join(results)
            return msg, {
                "name": "iThink Logistics",
                "tool": "Cancel Order",
                "data": {"success": True, "results": resp_data},
            }
        else:
            html_message = resp.get("html_message", "Unknown error")
            return _error_response("Cancel Order", f"Cancellation failed: {html_message}")

    except Exception as e:
        logger.error("Error cancelling iThink order: %s", e)
        return _error_response("Cancel Order", f"Failed to cancel order: {e}")


@tool(response_format="content_and_artifact")
async def check_pincode_ithinklogistics(
    pincode: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Check if a pincode is serviceable by iThink Logistics. Returns available logistics partners and COD availability."""
    try:
        emit_update("Checking pincode serviceability...")
        access_token, secret_key, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Check Pincode", err)

        payload = {"data": {"pincode": pincode}}
        resp = await make_ithinklogistics_request(
            "/api_v3/pincode/check.json",
            payload,
            access_token,
            secret_key,
            base_url=BASE_URL_ORDERS,
        )

        data = resp.get("data", {})
        if resp.get("status") == "success" or resp.get("status_code") == 200:
            msg = f"Pincode {pincode} serviceability:\n"
            if isinstance(data, dict):
                for partner, info in data.items():
                    if isinstance(info, dict):
                        available = info.get("available", "N/A")
                        cod = info.get("cod", "N/A")
                        msg += f"  {partner}: Available={available}, COD={cod}\n"
                    else:
                        msg += f"  {partner}: {info}\n"
            else:
                msg += str(data)

            return msg, {
                "name": "iThink Logistics",
                "tool": "Check Pincode",
                "data": {"success": True, "pincode": pincode, "serviceability": data},
            }
        else:
            return _error_response("Check Pincode", f"Pincode check failed for {pincode}.")

    except Exception as e:
        logger.error("Error checking pincode: %s", e)
        return _error_response("Check Pincode", f"Failed to check pincode: {e}")


@tool(response_format="content_and_artifact")
async def get_order_details_ithinklogistics(
    awb_number: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Get details of an existing order on iThink Logistics by AWB number.
    Returns order info including weight, dimensions, payment, and customer details."""
    try:
        emit_update("Fetching order details from iThink Logistics...")
        access_token, secret_key, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Order Details", err)

        payload = {"data": {"awb_number_list": awb_number}}
        resp = await make_ithinklogistics_request(
            "/api_v3/order/details.json",
            payload,
            access_token,
            secret_key,
            base_url=BASE_URL_ORDERS,
        )

        data = resp.get("data", {})
        if not data:
            return _error_response("Get Order Details", f"No details found for AWB {awb_number}.")

        awb_data = data.get(awb_number, {})
        if not awb_data:
            return _error_response("Get Order Details", f"No details for AWB {awb_number}.")

        order = awb_data.get("order_details", {})
        customer = awb_data.get("customer_details", {})

        lines = [
            f"AWB: {awb_number}",
            f"Order Number: {order.get('order_number', 'N/A')}",
            f"Order Type: {order.get('order_type', 'N/A')}",
            f"Weight: {order.get('phy_weight', 'N/A')} kg",
            f"Payment: {order.get('net_payment', 'N/A')}",
            f"Customer: {customer.get('customer_name', 'N/A')}",
            f"City: {customer.get('customer_city', 'N/A')}",
            f"Pincode: {customer.get('customer_pincode', 'N/A')}",
        ]
        msg = "\n".join(lines)
        return msg, {
            "name": "iThink Logistics",
            "tool": "Get Order Details",
            "data": {"success": True, "details": awb_data},
        }
    except Exception as e:
        logger.error("Error getting iThink order details: %s", e)
        return _error_response("Get Order Details", f"Failed to get order details: {e}")


@tool(response_format="content_and_artifact")
async def get_shipping_rate_ithinklogistics(
    origin_pincode: str,
    destination_pincode: str,
    weight: float,
    length: float,
    width: float,
    height: float,
    config: RunnableConfig,
    payment_mode: str = "cod",
) -> Tuple[str, Any]:
    """Get shipping rate estimates from iThink Logistics.

    Parameters:
    - origin_pincode: Sender/warehouse pincode
    - destination_pincode: Recipient pincode
    - weight: Package weight in kg
    - length, width, height: Package dimensions in cm
    - payment_mode: "cod" or "Prepaid" (default: "cod")
    """
    try:
        emit_update("Fetching shipping rates from iThink Logistics...")
        access_token, secret_key, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Shipping Rate", err)

        payload = {
            "data": {
                "origin_pincode": origin_pincode,
                "destination_pincode": destination_pincode,
                "weight": weight,
                "length": length,
                "width": width,
                "height": height,
                "payment_mode": payment_mode,
            }
        }
        resp = await make_ithinklogistics_request(
            "/api_v3/rate/check.json",
            payload,
            access_token,
            secret_key,
            base_url=BASE_URL_ORDERS,
        )

        data = resp.get("data", {})
        if resp.get("status") == "success" or resp.get("status_code") == 200:
            lines = [f"Shipping rates ({origin_pincode} → {destination_pincode}, {weight}kg):"]
            if isinstance(data, dict):
                for partner, rate_info in data.items():
                    if isinstance(rate_info, dict):
                        rate = rate_info.get("rate", "N/A")
                        etd = rate_info.get("etd", "N/A")
                        lines.append(f"  {partner}: Rs.{rate} (ETD: {etd} days)")
                    else:
                        lines.append(f"  {partner}: {rate_info}")
            else:
                lines.append(str(data))

            msg = "\n".join(lines)
            return msg, {
                "name": "iThink Logistics",
                "tool": "Get Shipping Rate",
                "data": {"success": True, "rates": data},
            }
        else:
            return _error_response("Get Shipping Rate", "Failed to fetch shipping rates.")

    except Exception as e:
        logger.error("Error getting shipping rate: %s", e)
        return _error_response("Get Shipping Rate", f"Failed to get shipping rate: {e}")


def get_ithinklogistics_tools() -> List[BaseTool]:
    return [
        track_order_ithinklogistics,
        track_international_order_ithinklogistics,
        create_order_ithinklogistics,
        cancel_order_ithinklogistics,
        check_pincode_ithinklogistics,
        get_order_details_ithinklogistics,
        get_shipping_rate_ithinklogistics,
    ]
