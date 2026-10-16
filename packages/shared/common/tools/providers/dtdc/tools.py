"""DTDC tools: track consignments, create shipments, cancel orders, check serviceability, get rates."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from common.tools.utils.writer import emit_update
from .utils import get_credentials_from_config, make_dtdc_request

logger = logging.getLogger(__name__)


def _error_response(tool_name: str, message: str) -> Tuple[str, Any]:
    return message, {
        "name": "DTDC",
        "tool": tool_name,
        "data": {"success": False, "message": message},
    }


def _format_tracking(track_data: Dict[str, Any]) -> str:
    """Format DTDC tracking data into a readable string."""
    lines = [
        f"Consignment No: {track_data.get('strShipmentNo', 'N/A')}",
        f"Status: {track_data.get('strStatus', 'Unknown')}",
        f"Origin: {track_data.get('strOrigin', 'N/A')}",
        f"Destination: {track_data.get('strDestination', 'N/A')}",
    ]

    delivery_date = track_data.get("strDeliveryDate")
    if delivery_date:
        lines.append(f"Delivered: {delivery_date}")

    activities = track_data.get("lstTrackDetail", [])
    if activities:
        lines.append("\nRecent Activity:")
        for act in activities[:5]:
            date = act.get("strActionDate", "")
            activity = act.get("strAction", "")
            location = act.get("strActionPlace", "")
            lines.append(f"  {date} — {activity} ({location})")

    return "\n".join(lines) if lines else "No tracking information available."


@tool(response_format="content_and_artifact")
async def track_order_dtdc(
    consignment_number: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Track a shipment on DTDC using its consignment number.
    Returns current status, origin, destination, and recent scan activity."""
    try:
        emit_update("Tracking consignment on DTDC...")
        api_key, customer_code, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Track Order", err)

        payload = {
            "TrkType": "cnno",
            "strcnno": consignment_number,
            "addtnlDtl": "Y",
        }

        resp = await make_dtdc_request(
            "/JSONCnTrk/getTrackDetails",
            api_key,
            method="POST",
            payload=payload,
        )

        track_details = resp.get("trackDetails", [])
        if not track_details:
            return _error_response("Track Order", f"No tracking data found for consignment {consignment_number}.")

        track = track_details[0]
        msg = _format_tracking(track)
        return msg, {
            "name": "DTDC",
            "tool": "Track Order",
            "data": {"success": True, "tracking": track},
        }
    except Exception as e:
        logger.error("Error tracking DTDC consignment: %s", e)
        return _error_response("Track Order", f"Failed to track consignment: {e}")


@tool(response_format="content_and_artifact")
async def create_order_dtdc(
    reference_number: str,
    consignee_name: str,
    consignee_phone: str,
    consignee_address: str,
    consignee_city: str,
    consignee_state: str,
    consignee_pincode: str,
    origin_city: str,
    origin_pincode: str,
    weight: float,
    declared_value: float,
    config: RunnableConfig,
    product_type: str = "Non Document",
    payment_mode: str = "Prepaid",
    num_pieces: int = 1,
) -> Tuple[str, Any]:
    """Create a new shipment order on DTDC.

    Parameters:
    - reference_number: Your unique order reference
    - consignee_name, consignee_phone, consignee_address, consignee_city, consignee_state, consignee_pincode: Recipient details
    - origin_city, origin_pincode: Origin details
    - weight: Actual weight in kg
    - declared_value: Declared value of the shipment
    - product_type: "Non Document" (default) or "Document"
    - payment_mode: "Prepaid" (default) or "COD"
    - num_pieces: Number of pieces (default: 1)
    """
    try:
        emit_update("Creating order on DTDC...")
        api_key, customer_code, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Create Order", err)

        order_payload = {
            "referenceNo": reference_number,
            "consigneeName": consignee_name,
            "consigneePhone": consignee_phone,
            "consigneeAddress": consignee_address,
            "consigneeCity": consignee_city,
            "consigneeState": consignee_state,
            "consigneePincode": consignee_pincode,
            "originCity": origin_city,
            "originPincode": origin_pincode,
            "actualWeight": weight,
            "declaredValue": declared_value,
            "productType": product_type,
            "paymentMode": payment_mode,
            "numPieces": num_pieces,
        }
        if customer_code:
            order_payload["customerCode"] = customer_code

        resp = await make_dtdc_request(
            "/JSONCnBooking/createBooking",
            api_key,
            method="POST",
            payload=order_payload,
        )

        consignment_no = resp.get("consignmentNumber") or resp.get("strCnno", "N/A")
        success = resp.get("success", False) or consignment_no != "N/A"

        if success:
            msg = (
                f"Order created successfully!\n"
                f"Consignment No: {consignment_no}\n"
                f"Reference: {reference_number}\n"
                f"Status: Booked"
            )
            return msg, {
                "name": "DTDC",
                "tool": "Create Order",
                "data": {"success": True, "consignment_number": consignment_no, "order_data": resp},
            }
        else:
            error_msg = resp.get("message", resp.get("errorMessage", "Unknown error"))
            return _error_response("Create Order", f"Order creation failed: {error_msg}")

    except Exception as e:
        logger.error("Error creating DTDC order: %s", e)
        return _error_response("Create Order", f"Failed to create order: {e}")


@tool(response_format="content_and_artifact")
async def cancel_order_dtdc(
    consignment_number: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Cancel a shipment on DTDC by its consignment number.

    Parameters:
    - consignment_number: DTDC consignment number to cancel
    """
    try:
        emit_update("Cancelling order on DTDC...")
        api_key, customer_code, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Cancel Order", err)

        payload = {"strCnno": consignment_number}
        if customer_code:
            payload["customerCode"] = customer_code

        resp = await make_dtdc_request(
            "/JSONCnCancel/cancelBooking",
            api_key,
            method="POST",
            payload=payload,
        )

        if resp.get("success") or resp.get("status"):
            msg = f"Cancellation result: Consignment {consignment_number} cancelled successfully."
            return msg, {
                "name": "DTDC",
                "tool": "Cancel Order",
                "data": {"success": True, "result": resp},
            }
        else:
            error_msg = resp.get("message", resp.get("errorMessage", "Cancellation failed"))
            return _error_response("Cancel Order", f"Cancellation failed: {error_msg}")

    except Exception as e:
        logger.error("Error cancelling DTDC order: %s", e)
        return _error_response("Cancel Order", f"Failed to cancel order: {e}")


@tool(response_format="content_and_artifact")
async def check_serviceability_dtdc(
    origin_pincode: str,
    destination_pincode: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Check if a route is serviceable by DTDC between two pincodes.

    Parameters:
    - origin_pincode: Origin pincode
    - destination_pincode: Destination pincode
    """
    try:
        emit_update("Checking serviceability on DTDC...")
        api_key, customer_code, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Check Serviceability", err)

        payload = {
            "orgPincode": origin_pincode,
            "desPincode": destination_pincode,
        }

        resp = await make_dtdc_request(
            "/JSONCnPincode/getPincodeDetails",
            api_key,
            method="POST",
            payload=payload,
        )

        pincode_details = resp.get("pincodeDetails", resp)
        if not pincode_details:
            return _error_response(
                "Check Serviceability",
                f"No serviceability data for {origin_pincode} → {destination_pincode}.",
            )

        serviceable = resp.get("isServiceable") or resp.get("success")
        if isinstance(pincode_details, dict):
            lines = [
                f"Route {origin_pincode} → {destination_pincode}:",
                f"  Serviceable: {'Yes' if serviceable else 'No'}",
                f"  Service Type: {pincode_details.get('serviceType', 'N/A')}",
                f"  Delivery TAT: {pincode_details.get('deliveryTat', 'N/A')} days",
            ]
        else:
            lines = [f"Route {origin_pincode} → {destination_pincode}: {'Serviceable' if serviceable else 'Not serviceable'}"]

        msg = "\n".join(lines)
        return msg, {
            "name": "DTDC",
            "tool": "Check Serviceability",
            "data": {"success": bool(serviceable), "details": pincode_details},
        }
    except Exception as e:
        logger.error("Error checking DTDC serviceability: %s", e)
        return _error_response("Check Serviceability", f"Failed to check serviceability: {e}")


@tool(response_format="content_and_artifact")
async def get_shipping_rate_dtdc(
    origin_pincode: str,
    destination_pincode: str,
    weight: float,
    config: RunnableConfig,
    cod: bool = False,
    declared_value: Optional[float] = None,
) -> Tuple[str, Any]:
    """Get shipping rate estimate from DTDC for a route.

    Parameters:
    - origin_pincode: Origin pincode
    - destination_pincode: Destination pincode
    - weight: Package weight in kg
    - cod: Whether the order is Cash on Delivery (default: false)
    - declared_value: Optional declared value
    """
    try:
        emit_update("Fetching shipping rates from DTDC...")
        api_key, customer_code, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Shipping Rate", err)

        payload: Dict[str, Any] = {
            "orgPincode": origin_pincode,
            "desPincode": destination_pincode,
            "actualWeight": str(weight),
            "paymentMode": "COD" if cod else "Prepaid",
        }
        if declared_value is not None:
            payload["declaredValue"] = str(declared_value)
        if customer_code:
            payload["customerCode"] = customer_code

        resp = await make_dtdc_request(
            "/JSONCnRateEstimate/getRateEstimate",
            api_key,
            method="POST",
            payload=payload,
        )

        if not resp:
            return _error_response("Get Shipping Rate", "No shipping rates available for this route.")

        rate_details = resp.get("rateDetails", resp)
        total = rate_details.get("totalAmount") or rate_details.get("total_amount", "N/A")

        lines = [f"Shipping rate ({origin_pincode} → {destination_pincode}, {weight}kg):"]
        lines.append(f"  Total: Rs.{total}")

        if isinstance(rate_details, dict):
            for key in ["freight", "serviceTax", "fuelSurcharge", "codCharge"]:
                val = rate_details.get(key)
                if val is not None:
                    lines.append(f"  {key}: Rs.{val}")

        msg = "\n".join(lines)
        return msg, {
            "name": "DTDC",
            "tool": "Get Shipping Rate",
            "data": {"success": True, "rates": rate_details},
        }
    except Exception as e:
        logger.error("Error getting DTDC shipping rate: %s", e)
        return _error_response("Get Shipping Rate", f"Failed to get shipping rate: {e}")


@tool(response_format="content_and_artifact")
async def get_order_details_dtdc(
    consignment_number: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Get details of an existing consignment on DTDC by its consignment number.
    Returns shipment info including status, origin, destination, and scan history."""
    try:
        emit_update("Fetching consignment details from DTDC...")
        api_key, customer_code, err = await get_credentials_from_config(config)
        if err:
            return _error_response("Get Order Details", err)

        payload = {
            "TrkType": "cnno",
            "strcnno": consignment_number,
            "addtnlDtl": "Y",
        }

        resp = await make_dtdc_request(
            "/JSONCnTrk/getTrackDetails",
            api_key,
            method="POST",
            payload=payload,
        )

        track_details = resp.get("trackDetails", [])
        if not track_details:
            return _error_response("Get Order Details", f"No details found for consignment {consignment_number}.")

        track = track_details[0]
        msg = _format_tracking(track)
        return msg, {
            "name": "DTDC",
            "tool": "Get Order Details",
            "data": {"success": True, "details": track},
        }
    except Exception as e:
        logger.error("Error getting DTDC order details: %s", e)
        return _error_response("Get Order Details", f"Failed to get order details: {e}")


def get_dtdc_tools() -> List[BaseTool]:
    return [
        track_order_dtdc,
        create_order_dtdc,
        cancel_order_dtdc,
        check_serviceability_dtdc,
        get_order_details_dtdc,
        get_shipping_rate_dtdc,
    ]
