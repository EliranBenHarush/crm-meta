import os
import requests

PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "1061417953715616")
WABA_ID = os.getenv("WABA_ID")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
GRAPH_VERSION = "v26.0"


def _headers():
    if not WHATSAPP_TOKEN:
        raise Exception("WHATSAPP_TOKEN is not configured")

    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }


def send_whatsapp_message(to: str, text: str):
    url = (
        f"https://graph.facebook.com/{GRAPH_VERSION}/"
        f"{PHONE_NUMBER_ID}/messages"
    )

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": text
        }
    }

    return requests.post(
        url,
        headers=_headers(),
        json=payload,
        timeout=30
    )


def get_message_templates():
    if not WABA_ID:
        raise Exception("WABA_ID is not configured")

    url = (
        f"https://graph.facebook.com/{GRAPH_VERSION}/"
        f"{WABA_ID}/message_templates"
    )

    params = {
        "fields": "id,name,status,language,category,components",
        "limit": 100
    }

    return requests.get(
        url,
        headers=_headers(),
        params=params,
        timeout=30
    )


def send_whatsapp_template(
    to: str,
    template_name: str,
    language: str,
    body_parameters=None
):
    url = (
        f"https://graph.facebook.com/{GRAPH_VERSION}/"
        f"{PHONE_NUMBER_ID}/messages"
    )

    template = {
        "name": template_name,
        "language": {
            "code": language
        }
    }

    parameters = body_parameters or []

    if parameters:
        template["components"] = [
            {
                "type": "body",
                "parameters": [
                    {
                        "type": "text",
                        "text": str(value)
                    }
                    for value in parameters
                ]
            }
        ]

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template
    }

    return requests.post(
        url,
        headers=_headers(),
        json=payload,
        timeout=30
    )
