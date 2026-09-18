import os
import requests

PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "1061417953715616")
WABA_ID = os.getenv("WABA_ID")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
GRAPH_VERSION = "v26.0"


def _auth_headers():
    if not WHATSAPP_TOKEN:
        raise Exception("WHATSAPP_TOKEN is not configured")

    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}"
    }


def _headers():
    return {
        **_auth_headers(),
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


def upload_whatsapp_media(filename: str, content: bytes, content_type: str):
    url = (
        f"https://graph.facebook.com/{GRAPH_VERSION}/"
        f"{PHONE_NUMBER_ID}/media"
    )

    return requests.post(
        url,
        headers=_auth_headers(),
        data={"messaging_product": "whatsapp"},
        files={"file": (filename, content, content_type)},
        timeout=60
    )


def send_whatsapp_media(
    to: str,
    media_type: str,
    media_id: str,
    caption: str | None = None,
    filename: str | None = None
):
    if media_type not in {"image", "video", "audio", "document"}:
        raise ValueError("Unsupported media type")

    media = {"id": media_id}

    if caption and media_type in {"image", "video", "document"}:
        media["caption"] = caption

    if filename and media_type == "document":
        media["filename"] = filename

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": media_type,
        media_type: media
    }

    url = (
        f"https://graph.facebook.com/{GRAPH_VERSION}/"
        f"{PHONE_NUMBER_ID}/messages"
    )

    return requests.post(
        url,
        headers=_headers(),
        json=payload,
        timeout=30
    )


def get_whatsapp_media(media_id: str):
    meta_url = (
        f"https://graph.facebook.com/{GRAPH_VERSION}/"
        f"{media_id}"
    )

    meta_response = requests.get(
        meta_url,
        headers=_auth_headers(),
        timeout=30
    )

    if meta_response.status_code >= 400:
        return meta_response, None

    meta_data = meta_response.json()
    download_url = meta_data.get("url")

    if not download_url:
        return meta_response, None

    file_response = requests.get(
        download_url,
        headers=_auth_headers(),
        timeout=60
    )

    return meta_response, file_response
