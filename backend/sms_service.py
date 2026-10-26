"""
SMS Notification Service — Twilio Integration
==============================================
Sends emergency SMS alerts to family members / emergency contacts.
"""

import os
import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

logger = logging.getLogger(__name__)

# ── Twilio Config ──
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

_client = None


def _get_client():
    """Lazily initialize the Twilio client."""
    global _client
    if _client is not None:
        return _client
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
        logger.error("Twilio credentials not configured — SMS disabled")
        return None
    try:
        _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("Twilio client initialized successfully")
        return _client
    except Exception as e:
        logger.error(f"Failed to initialize Twilio client: {e}")
        return None


def format_emergency_sms(user_id, trigger_type, lat, lng, digipin, dashboard_url=""):
    """Format the emergency SMS message."""
    maps_link = f"https://www.google.com/maps?q={lat},{lng}"
    trigger_text = (
        "FALL DETECTED (no response)"
        if trigger_type == "fall"
        else "MANUAL PANIC BUTTON"
    )

    msg = (
        f"🚨 EMERGENCY ALERT 🚨\n\n"
        f"User: {user_id}\n"
        f"Trigger: {trigger_text}\n\n"
        f"📍 Location:\n"
        f"GPS: {lat:.6f}, {lng:.6f}\n"
        f"DigiPIN: {digipin}\n"
        f"Maps: {maps_link}\n"
    )

    if dashboard_url:
        msg += f"\n🖥️ Live Dashboard:\n{dashboard_url}\n"

    msg += "\n⚠️ Please check on them immediately!"
    return msg

def normalize_phone(number):
    """Ensure phone number is in E.164 format for Twilio."""
    number = number.strip().replace(" ", "").replace("-", "")
    if not number.startswith("+"):
        # Indian 10-digit number — add +91
        if len(number) == 10 and number.isdigit():
            number = "+91" + number
        # Already has country code but missing +
        elif len(number) > 10 and number.isdigit():
            number = "+" + number
    return number


def send_sms(to_number, message):
    """Send an SMS via Twilio. Returns a dict with status info."""
    to_number = normalize_phone(to_number)
    client = _get_client()
    if not client:
        return {"success": False, "error": "Twilio client not initialized", "to": to_number}

    try:
        msg = client.messages.create(
            body=message,
            from_=TWILIO_PHONE_NUMBER,
            to=to_number,
        )
        logger.info(f"SMS sent to {to_number} — SID: {msg.sid}")
        return {"success": True, "sid": msg.sid, "to": to_number, "status": msg.status}
    except TwilioRestException as e:
        logger.error(f"Twilio error sending to {to_number}: {e}")
        return {"success": False, "error": str(e), "to": to_number}
    except Exception as e:
        logger.error(f"Unexpected error sending SMS to {to_number}: {e}")
        return {"success": False, "error": str(e), "to": to_number}


def send_emergency_alerts(contacts, user_id, trigger_type, lat, lng, digipin, dashboard_url=""):
    """Send emergency SMS to all contacts. Returns list of send results."""
    message = format_emergency_sms(user_id, trigger_type, lat, lng, digipin, dashboard_url)

    results = []
    for contact in contacts:
        phone = contact.get("phone_number", "")
        name = contact.get("name", "Unknown")
        if not phone:
            results.append({"success": False, "error": "No phone number", "contact": name})
            continue

        result = send_sms(phone, message)
        result["contact"] = name
        results.append(result)
        logger.info(f"Alert to {name} ({phone}): {'sent' if result['success'] else 'failed'}")

    return results
