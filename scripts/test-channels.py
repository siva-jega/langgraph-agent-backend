import hmac
import hashlib
import json
import time
import uuid
import threading
import os
import requests

WEBHOOK_URL = "http://localhost:9500/webhooks/meta"
APP_SECRET = os.getenv("META_APP_SECRET", "")

PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
WABA_ID = os.getenv("META_WABA_ID", "")
FROM_NUMBER = os.getenv("META_TEST_FROM_NUMBER", "")

MESSAGES = [
    "Hi there!",
    "Recommend me some products",
    "Recommend me some tees",
    "Thanks for your help",
]


def build_payload(message_text: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA_ID,
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "916300588670",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test User"},
                                    "wa_id": FROM_NUMBER,
                                }
                            ],
                            "messages": [
                                {
                                    "from": FROM_NUMBER,
                                    "id": f"wamid.{uuid.uuid4().hex.upper()}",
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": message_text},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def sign_payload(body_bytes: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), body_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def send(i: int, msg: str) -> None:
    payload = build_payload(msg)
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = sign_payload(body, APP_SECRET)
    resp = requests.post(
        WEBHOOK_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-hub-signature-256": signature,
        },
    )
    print(f"[{i}] '{msg}' → {resp.status_code} {resp.text}")


threads = [threading.Thread(target=send, args=(i, msg)) for i, msg in enumerate(MESSAGES, 1)]
for t in threads:
    t.start()
for t in threads:
    t.join()
