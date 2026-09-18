import os
import requests

PHONE_NUMBER_ID = "1061417953715616"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")


def send_whatsapp_message(to: str, text: str):
    if not WHATSAPP_TOKEN:
        raise Exception("WHATSAPP_TOKEN is not configured")

    url = (
        f"https://graph.facebook.com/v26.0/"
        f"{PHONE_NUMBER_ID}/messages"
    )

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": text
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    return response