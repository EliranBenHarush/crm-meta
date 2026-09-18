import asyncio
import html
import json
import os
import requests
import imageio_ffmpeg
import subprocess
import tempfile

from fastapi import FastAPI, Request, Response, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import inspect, text as sql_text
from datetime import datetime

from database import Base, engine, SessionLocal
from models import Contact, Message, ConversationState, ContactNote, ContactTag, ContactAssignment, FollowUpReminder, BroadcastRun, BroadcastDelivery
from whatsapp import send_whatsapp_message, get_message_templates, send_whatsapp_template, upload_whatsapp_media, send_whatsapp_media, get_whatsapp_media


Base.metadata.create_all(bind=engine)

# Lightweight migration for existing databases until Alembic is added.
with engine.begin() as connection:
    existing_columns = {
        column["name"]
        for column in inspect(engine).get_columns("messages")
    }

    if "media_id" not in existing_columns:
        connection.execute(
            sql_text("ALTER TABLE messages ADD COLUMN media_id VARCHAR")
        )

    if "media_mime" not in existing_columns:
        connection.execute(
            sql_text("ALTER TABLE messages ADD COLUMN media_mime VARCHAR")
        )

    if "media_filename" not in existing_columns:
        connection.execute(
            sql_text("ALTER TABLE messages ADD COLUMN media_filename VARCHAR")
        )

    if "delivery_status" not in existing_columns:
        connection.execute(
            sql_text("ALTER TABLE messages ADD COLUMN delivery_status VARCHAR")
        )

    if "status_updated_at" not in existing_columns:
        connection.execute(
            sql_text("ALTER TABLE messages ADD COLUMN status_updated_at TIMESTAMP")
        )

app = FastAPI(title="Arcadia CRM API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "arcadia_crm_verify_2026")
SHOP_BASE_URL = os.getenv("SHOP_BASE_URL", "https://arcdia.co.il").rstrip("/")

# One Railway replica is currently used, so an in-memory SSE broadcaster is enough.
# If we scale to multiple backend replicas later, replace this with Redis Pub/Sub.
sse_clients = set()


async def broadcast_event(event):
    dead_clients = []

    for queue in list(sse_clients):
        try:
            queue.put_nowait(event)
        except Exception:
            dead_clients.append(queue)

    for queue in dead_clients:
        sse_clients.discard(queue)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def _format_store_price(prices):
    prices = prices or {}
    raw = prices.get("price")
    currency = prices.get("currency_symbol") or "₪"
    minor = prices.get("currency_minor_unit")

    if raw in (None, ""):
        return None

    try:
        minor = int(minor if minor is not None else 2)
        amount = int(raw) / (10 ** minor)
        if amount.is_integer():
            amount_text = f"{int(amount):,}"
        else:
            amount_text = f"{amount:,.2f}"
        return f"{currency}{amount_text}"
    except Exception:
        return f"{currency}{raw}"


def _normalize_store_product(product):
    images = product.get("images") or []
    image_url = images[0].get("src") if images else None

    return {
        "id": product.get("id"),
        "name": html.unescape(product.get("name") or ""),
        "price": _format_store_price(product.get("prices")),
        "image": image_url,
        "permalink": product.get("permalink"),
        "sku": product.get("sku"),
        "is_in_stock": product.get("is_in_stock"),
    }


def _store_get(path, params=None):
    url = f"{SHOP_BASE_URL}/wp-json/wc/store/v1/{path.lstrip('/')}"
    return requests.get(
        url,
        params=params,
        timeout=30,
        headers={"User-Agent": "Arcadia-CRM/1.0"}
    )


@app.get("/")
def home():
    return {
        "status": "ok",
        "app": "Arcadia CRM"
    }


@app.get("/events")
async def events(request: Request):
    queue = asyncio.Queue()
    sse_clients.add(queue)

    async def event_stream():
        try:
            # Tell the browser the stream is connected.
            yield 'event: connected\ndata: {"status":"ok"}\n\n'

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                except asyncio.TimeoutError:
                    # Keep the connection alive through proxies/load balancers.
                    yield ": keepalive\n\n"
        finally:
            sse_clients.discard(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(
            content=challenge,
            media_type="text/plain"
        )

    return Response(
        content="Verification failed",
        status_code=403
    )


@app.post("/webhook/whatsapp")
async def receive_whatsapp(
    request: Request,
    db: Session = Depends(get_db)
):
    payload = await request.json()

    print("WHATSAPP EVENT:")
    print(payload)

    try:
        entry = payload.get("entry", [])

        if not entry:
            return {"status": "ignored"}

        changes = entry[0].get("changes", [])

        if not changes:
            return {"status": "ignored"}

        value = changes[0].get("value", {})

        statuses = value.get("statuses", [])

        if statuses:
            updated = 0

            for status_event in statuses:
                whatsapp_message_id = status_event.get("id")
                delivery_status = status_event.get("status")

                if not whatsapp_message_id or not delivery_status:
                    continue

                saved_message = db.query(Message).filter(
                    Message.whatsapp_message_id == whatsapp_message_id
                ).first()

                if not saved_message:
                    continue

                saved_message.delivery_status = delivery_status
                saved_message.status_updated_at = datetime.utcnow()
                updated += 1

                contact = db.query(Contact).filter(
                    Contact.id == saved_message.contact_id
                ).first()

                if contact:
                    await broadcast_event({
                        "type": "message_status",
                        "phone": contact.phone,
                        "message_id": saved_message.id,
                        "delivery_status": delivery_status,
                    })

            if updated:
                db.commit()

            return {
                "status": "status_updated",
                "updated": updated
            }

        messages = value.get("messages", [])

        if not messages:
            return {"status": "ignored"}

        message = messages[0]

        phone = message.get("from")
        whatsapp_message_id = message.get("id")
        message_type = message.get("type", "unknown")

        body = None
        media_id = None
        media_mime = None
        media_filename = None

        if message_type == "text":
            body = message.get("text", {}).get("body")
        elif message_type in {"image", "video", "audio", "document"}:
            media_payload = message.get(message_type, {}) or {}
            media_id = media_payload.get("id")
            media_mime = media_payload.get("mime_type")
            media_filename = media_payload.get("filename")
            body = media_payload.get("caption") or media_filename

        contacts = value.get("contacts", [])

        name = None

        if contacts:
            name = contacts[0].get(
                "profile",
                {}
            ).get("name")

        contact = db.query(Contact).filter(
            Contact.phone == phone
        ).first()

        if not contact:
            contact = Contact(
                phone=phone,
                name=name
            )

            db.add(contact)
            db.commit()
            db.refresh(contact)

        else:
            if name:
                contact.name = name

            contact.updated_at = datetime.utcnow()

            db.commit()

        existing_message = db.query(Message).filter(
            Message.whatsapp_message_id == whatsapp_message_id
        ).first()

        if existing_message:
            return {"status": "duplicate"}

        new_message = Message(
            contact_id=contact.id,
            whatsapp_message_id=whatsapp_message_id,
            direction="incoming",
            message_type=message_type,
            body=body,
            media_id=media_id,
            media_mime=media_mime,
            media_filename=media_filename
        )

        db.add(new_message)

        conversation_state = db.query(ConversationState).filter(
            ConversationState.contact_id == contact.id
        ).first()

        if not conversation_state:
            conversation_state = ConversationState(
                contact_id=contact.id,
                unread_count=0
            )
            db.add(conversation_state)

        conversation_state.unread_count = (
            conversation_state.unread_count or 0
        ) + 1

        db.commit()
        db.refresh(new_message)
        db.refresh(contact)
        db.refresh(conversation_state)

        await broadcast_event({
            "type": "new_message",
            "phone": phone,
            "contact_id": contact.id,
            "name": contact.name,
            "status": contact.status,
            "message": {
                "id": new_message.id,
                "direction": new_message.direction,
                "type": new_message.message_type,
                "body": new_message.body,
                "media_id": new_message.media_id,
                "media_mime": new_message.media_mime,
                "media_filename": new_message.media_filename,
                "created_at": new_message.created_at,
            },
            "updated_at": contact.updated_at,
        })

        return {
            "status": "saved"
        }

    except Exception as e:
        print("WEBHOOK ERROR:", e)

        return {
            "status": "error",
            "error": str(e)
        }


@app.get("/conversations")
def get_conversations(
    db: Session = Depends(get_db)
):
    contacts = db.query(Contact).order_by(
        Contact.updated_at.desc()
    ).all()

    result = []

    for contact in contacts:
        last_message = (
            db.query(Message)
            .filter(Message.contact_id == contact.id)
            .order_by(Message.created_at.desc())
            .first()
        )

        conversation_state = db.query(ConversationState).filter(
            ConversationState.contact_id == contact.id
        ).first()

        result.append({
            "id": contact.id,
            "name": contact.name,
            "phone": contact.phone,
            "status": contact.status,
            "last_message": (
                last_message.body if last_message else None
            ),
            "last_direction": (
                last_message.direction if last_message else None
            ),
            "unread_count": (
                conversation_state.unread_count
                if conversation_state else 0
            ),
            "updated_at": contact.updated_at
        })

    return result


@app.post("/contacts/open")
async def open_contact(
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    raw_phone = (data.get("phone") or "").strip()
    name = (data.get("name") or "").strip() or None

    phone = "".join(ch for ch in raw_phone if ch.isdigit())

    if phone.startswith("0"):
        phone = "972" + phone[1:]

    if not phone:
        raise HTTPException(
            status_code=400,
            detail="phone is required"
        )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        contact = Contact(
            phone=phone,
            name=name
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
    elif name and not contact.name:
        contact.name = name
        contact.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(contact)

    return {
        "id": contact.id,
        "name": contact.name,
        "phone": contact.phone,
        "status": contact.status,
        "last_message": None,
        "last_direction": None,
        "unread_count": 0,
        "updated_at": contact.updated_at,
    }


@app.get("/messages/{phone}")
def get_messages(
    phone: str,
    db: Session = Depends(get_db)
):
    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    messages = (
        db.query(Message)
        .filter(Message.contact_id == contact.id)
        .order_by(Message.created_at.asc())
        .all()
    )

    return [
        {
            "id": message.id,
            "direction": message.direction,
            "type": message.message_type,
            "body": message.body,
            "media_id": message.media_id,
            "media_mime": message.media_mime,
            "media_filename": message.media_filename,
            "delivery_status": message.delivery_status,
            "status_updated_at": message.status_updated_at,
            "created_at": message.created_at
        }
        for message in messages
    ]


@app.delete("/messages/{message_id}")
async def delete_outgoing_message(
    message_id: int,
    db: Session = Depends(get_db)
):
    message = db.query(Message).filter(
        Message.id == message_id
    ).first()

    if not message:
        raise HTTPException(
            status_code=404,
            detail="Message not found"
        )

    if message.direction != "outgoing":
        raise HTTPException(
            status_code=400,
            detail="Only outgoing messages can be deleted from CRM"
        )

    contact = db.query(Contact).filter(
        Contact.id == message.contact_id
    ).first()

    phone = contact.phone if contact else None

    db.delete(message)
    db.commit()

    if contact:
        last_message = (
            db.query(Message)
            .filter(Message.contact_id == contact.id)
            .order_by(Message.created_at.desc())
            .first()
        )

        contact.updated_at = (
            last_message.created_at
            if last_message else datetime.utcnow()
        )
        db.commit()

    if phone:
        await broadcast_event({
            "type": "message_deleted",
            "phone": phone,
            "message_id": message_id,
        })

    return {
        "success": True,
        "message_id": message_id
    }


@app.post("/send-message")
async def send_message(
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()

    phone = data.get("phone")
    text = data.get("text")

    if not phone or not text:
        raise HTTPException(
            status_code=400,
            detail="phone and text are required"
        )

    response = send_whatsapp_message(
        phone,
        text
    )

    api_data = response.json()

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=api_data
        )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        contact = Contact(
            phone=phone
        )

        db.add(contact)
        db.commit()
        db.refresh(contact)

    whatsapp_message_id = None

    if api_data.get("messages"):
        whatsapp_message_id = (
            api_data["messages"][0].get("id")
        )

    message = Message(
        contact_id=contact.id,
        whatsapp_message_id=whatsapp_message_id,
        direction="outgoing",
        message_type="text",
        body=text,
        delivery_status="accepted",
        status_updated_at=datetime.utcnow()
    )

    db.add(message)

    contact.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(message)
    db.refresh(contact)

    await broadcast_event({
        "type": "new_message",
        "phone": phone,
        "contact_id": contact.id,
        "name": contact.name,
        "status": contact.status,
        "message": {
            "id": message.id,
            "direction": message.direction,
            "type": message.message_type,
            "body": message.body,
            "media_id": message.media_id,
            "media_mime": message.media_mime,
            "media_filename": message.media_filename,
            "created_at": message.created_at,
        },
        "updated_at": contact.updated_at,
    })

    return {
        "success": True,
        "whatsapp": api_data
    }


@app.get("/media/{media_id}")
def fetch_media(media_id: str):
    meta_response, file_response = get_whatsapp_media(media_id)

    if meta_response.status_code >= 400:
        try:
            detail = meta_response.json()
        except Exception:
            detail = {"raw": meta_response.text}

        raise HTTPException(
            status_code=meta_response.status_code,
            detail=detail
        )

    if file_response is None:
        raise HTTPException(
            status_code=404,
            detail="Media URL not found"
        )

    if file_response.status_code >= 400:
        raise HTTPException(
            status_code=file_response.status_code,
            detail="Failed to download media"
        )

    mime_type = (
        meta_response.json().get("mime_type")
        or file_response.headers.get("Content-Type")
        or "application/octet-stream"
    )

    return Response(
        content=file_response.content,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, max-age=300"
        }
    )


def _convert_webm_audio_to_mp3(content: bytes):
    input_path = None
    output_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".webm",
            delete=False
        ) as input_file:
            input_file.write(content)
            input_path = input_file.name

        with tempfile.NamedTemporaryFile(
            suffix=".mp3",
            delete=False
        ) as output_file:
            output_path = output_file.name

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                input_path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-b:a",
                "64k",
                output_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )

        with open(output_path, "rb") as converted:
            return converted.read()
    finally:
        for path in (input_path, output_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


@app.post("/send-media")
async def send_media(
    phone: str = Form(...),
    file: UploadFile = File(...),
    caption: str = Form(""),
    db: Session = Depends(get_db)
):
    if not phone:
        raise HTTPException(
            status_code=400,
            detail="phone is required"
        )

    content = await file.read()

    if not content:
        raise HTTPException(
            status_code=400,
            detail="File is empty"
        )

    content_type = file.content_type or "application/octet-stream"
    upload_filename = file.filename or "file"

    if content_type.startswith("audio/webm") or content_type == "video/webm":
        try:
            content = _convert_webm_audio_to_mp3(content)
            content_type = "audio/mpeg"
            upload_filename = "voice-message.mp3"
        except Exception as error:
            print("VOICE CONVERSION ERROR:", error)

    if content_type.startswith("image/"):
        media_type = "image"
    elif content_type.startswith("video/"):
        media_type = "video"
    elif content_type.startswith("audio/"):
        media_type = "audio"
    else:
        media_type = "document"

    upload_response = upload_whatsapp_media(
        upload_filename,
        content,
        content_type
    )

    try:
        upload_data = upload_response.json()
    except Exception:
        upload_data = {"raw": upload_response.text}

    if upload_response.status_code >= 400:
        raise HTTPException(
            status_code=upload_response.status_code,
            detail=upload_data
        )

    media_id = upload_data.get("id")

    if not media_id:
        raise HTTPException(
            status_code=502,
            detail="WhatsApp did not return a media id"
        )

    send_response = send_whatsapp_media(
        phone,
        media_type,
        media_id,
        caption=caption.strip() or None,
        filename=upload_filename
    )

    try:
        api_data = send_response.json()
    except Exception:
        api_data = {"raw": send_response.text}

    if send_response.status_code >= 400:
        raise HTTPException(
            status_code=send_response.status_code,
            detail=api_data
        )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        contact = Contact(phone=phone)
        db.add(contact)
        db.commit()
        db.refresh(contact)

    whatsapp_message_id = None

    if api_data.get("messages"):
        whatsapp_message_id = (
            api_data["messages"][0].get("id")
        )

    display_body = caption.strip()

    if not display_body:
        display_body = (
            "🎤 הודעה קולית"
            if media_type == "audio"
            else file.filename or f"[{media_type}]"
        )

    message = Message(
        contact_id=contact.id,
        whatsapp_message_id=whatsapp_message_id,
        direction="outgoing",
        message_type=media_type,
        body=display_body,
        media_id=media_id,
        media_mime=content_type,
        media_filename=upload_filename,
        delivery_status="accepted",
        status_updated_at=datetime.utcnow()
    )

    db.add(message)
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    db.refresh(contact)

    await broadcast_event({
        "type": "new_message",
        "phone": phone,
        "contact_id": contact.id,
        "name": contact.name,
        "status": contact.status,
        "message": {
            "id": message.id,
            "direction": message.direction,
            "type": message.message_type,
            "body": message.body,
            "created_at": message.created_at,
        },
        "updated_at": contact.updated_at,
    })

    return {
        "success": True,
        "media_type": media_type,
        "media_id": media_id,
        "whatsapp": api_data
    }


ALLOWED_CONTACT_STATUSES = {
    "ליד חדש",
    "בטיפול",
    "הצעת מחיר",
    "נסגר",
    "לא רלוונטי",
}


@app.patch("/contacts/{phone}/status")
async def update_contact_status(
    phone: str,
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    status = data.get("status")

    if status not in ALLOWED_CONTACT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Invalid status"
        )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    contact.status = status
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(contact)

    await broadcast_event({
        "type": "contact_updated",
        "phone": contact.phone,
        "contact_id": contact.id,
        "name": contact.name,
        "status": contact.status,
        "updated_at": contact.updated_at,
    })

    return {
        "success": True,
        "phone": contact.phone,
        "status": contact.status
    }



@app.post("/contacts/{phone}/read")
async def mark_conversation_read(
    phone: str,
    db: Session = Depends(get_db)
):
    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    conversation_state = db.query(ConversationState).filter(
        ConversationState.contact_id == contact.id
    ).first()

    if not conversation_state:
        conversation_state = ConversationState(
            contact_id=contact.id,
            unread_count=0
        )
        db.add(conversation_state)
    else:
        conversation_state.unread_count = 0

    db.commit()
    db.refresh(conversation_state)

    await broadcast_event({
        "type": "conversation_read",
        "phone": contact.phone,
        "contact_id": contact.id,
        "unread_count": 0,
    })

    return {
        "success": True,
        "phone": contact.phone,
        "unread_count": 0
    }



@app.get("/contacts/{phone}/crm")
def get_contact_crm(
    phone: str,
    db: Session = Depends(get_db)
):
    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    notes = (
        db.query(ContactNote)
        .filter(ContactNote.contact_id == contact.id)
        .order_by(ContactNote.created_at.desc())
        .all()
    )

    tags = (
        db.query(ContactTag)
        .filter(ContactTag.contact_id == contact.id)
        .order_by(ContactTag.created_at.asc())
        .all()
    )

    assignment = db.query(ContactAssignment).filter(
        ContactAssignment.contact_id == contact.id
    ).first()

    reminders = (
        db.query(FollowUpReminder)
        .filter(FollowUpReminder.contact_id == contact.id)
        .order_by(FollowUpReminder.due_at.asc())
        .all()
    )

    return {
        "notes": [
            {
                "id": note.id,
                "body": note.body,
                "created_at": note.created_at,
            }
            for note in notes
        ],
        "tags": [
            {
                "id": tag.id,
                "name": tag.name,
                "created_at": tag.created_at,
            }
            for tag in tags
        ],
        "assignee": assignment.assignee if assignment else None,
        "reminders": [
            {
                "id": reminder.id,
                "note": reminder.note,
                "due_at": reminder.due_at,
                "completed_at": reminder.completed_at,
                "created_at": reminder.created_at,
            }
            for reminder in reminders
        ],
    }


@app.post("/contacts/{phone}/notes")
async def add_contact_note(
    phone: str,
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    body = (data.get("body") or "").strip()

    if not body:
        raise HTTPException(
            status_code=400,
            detail="Note body is required"
        )

    if len(body) > 2000:
        raise HTTPException(
            status_code=400,
            detail="Note is too long"
        )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    note = ContactNote(
        contact_id=contact.id,
        body=body
    )

    db.add(note)
    db.commit()
    db.refresh(note)

    await broadcast_event({
        "type": "contact_crm_updated",
        "phone": contact.phone,
        "contact_id": contact.id,
        "resource": "note",
    })

    return {
        "id": note.id,
        "body": note.body,
        "created_at": note.created_at,
    }


@app.post("/contacts/{phone}/tags")
async def add_contact_tag(
    phone: str,
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    name = (data.get("name") or "").strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Tag name is required"
        )

    if len(name) > 50:
        raise HTTPException(
            status_code=400,
            detail="Tag is too long"
        )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    existing = db.query(ContactTag).filter(
        ContactTag.contact_id == contact.id,
        ContactTag.name == name
    ).first()

    if existing:
        return {
            "id": existing.id,
            "name": existing.name,
            "created_at": existing.created_at,
        }

    tag = ContactTag(
        contact_id=contact.id,
        name=name
    )

    db.add(tag)
    db.commit()
    db.refresh(tag)

    await broadcast_event({
        "type": "contact_crm_updated",
        "phone": contact.phone,
        "contact_id": contact.id,
        "resource": "tag",
    })

    return {
        "id": tag.id,
        "name": tag.name,
        "created_at": tag.created_at,
    }


@app.delete("/contacts/{phone}/tags/{tag_id}")
async def delete_contact_tag(
    phone: str,
    tag_id: int,
    db: Session = Depends(get_db)
):
    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    tag = db.query(ContactTag).filter(
        ContactTag.id == tag_id,
        ContactTag.contact_id == contact.id
    ).first()

    if not tag:
        raise HTTPException(
            status_code=404,
            detail="Tag not found"
        )

    db.delete(tag)
    db.commit()

    await broadcast_event({
        "type": "contact_crm_updated",
        "phone": contact.phone,
        "contact_id": contact.id,
        "resource": "tag",
    })

    return {"success": True}



@app.patch("/contacts/{phone}/assignee")
async def update_contact_assignee(
    phone: str,
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    assignee = (data.get("assignee") or "").strip() or None

    if assignee and len(assignee) > 80:
        raise HTTPException(
            status_code=400,
            detail="Assignee name is too long"
        )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    assignment = db.query(ContactAssignment).filter(
        ContactAssignment.contact_id == contact.id
    ).first()

    if not assignment:
        assignment = ContactAssignment(
            contact_id=contact.id,
            assignee=assignee
        )
        db.add(assignment)
    else:
        assignment.assignee = assignee
        assignment.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(assignment)

    await broadcast_event({
        "type": "contact_crm_updated",
        "phone": contact.phone,
        "contact_id": contact.id,
        "resource": "assignee",
    })

    return {
        "success": True,
        "assignee": assignment.assignee
    }


@app.post("/contacts/{phone}/reminders")
async def add_follow_up_reminder(
    phone: str,
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    note = (data.get("note") or "").strip()
    due_at_raw = (data.get("due_at") or "").strip()

    if not note:
        raise HTTPException(
            status_code=400,
            detail="Reminder note is required"
        )

    if len(note) > 500:
        raise HTTPException(
            status_code=400,
            detail="Reminder note is too long"
        )

    try:
        due_at = datetime.fromisoformat(due_at_raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid reminder date"
        )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    reminder = FollowUpReminder(
        contact_id=contact.id,
        note=note,
        due_at=due_at
    )

    db.add(reminder)
    db.commit()
    db.refresh(reminder)

    await broadcast_event({
        "type": "contact_crm_updated",
        "phone": contact.phone,
        "contact_id": contact.id,
        "resource": "reminder",
    })

    return {
        "id": reminder.id,
        "note": reminder.note,
        "due_at": reminder.due_at,
        "completed_at": reminder.completed_at,
        "created_at": reminder.created_at,
    }


@app.patch("/contacts/{phone}/reminders/{reminder_id}/complete")
async def complete_follow_up_reminder(
    phone: str,
    reminder_id: int,
    db: Session = Depends(get_db)
):
    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Contact not found"
        )

    reminder = db.query(FollowUpReminder).filter(
        FollowUpReminder.id == reminder_id,
        FollowUpReminder.contact_id == contact.id
    ).first()

    if not reminder:
        raise HTTPException(
            status_code=404,
            detail="Reminder not found"
        )

    reminder.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(reminder)

    await broadcast_event({
        "type": "contact_crm_updated",
        "phone": contact.phone,
        "contact_id": contact.id,
        "resource": "reminder",
    })

    return {
        "success": True,
        "id": reminder.id,
        "completed_at": reminder.completed_at
    }



@app.get("/products/search")
def search_products(q: str = ""):
    params = {
        "per_page": 20,
        "order": "desc",
        "orderby": "date",
    }

    query = (q or "").strip()

    if query:
        params["search"] = query

    response = _store_get("products", params=params)

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Could not load products from the store",
                "store_response": data,
            }
        )

    if not isinstance(data, list):
        raise HTTPException(
            status_code=502,
            detail="Unexpected response from WooCommerce Store API"
        )

    return [_normalize_store_product(product) for product in data]


@app.post("/send-product")
async def send_product(
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    phone = (data.get("phone") or "").strip()
    product_id = data.get("product_id")
    custom_caption = data.get("caption")
    send_image = data.get("send_image", True)

    if not phone or not product_id:
        raise HTTPException(
            status_code=400,
            detail="phone and product_id are required"
        )

    product_response = _store_get(f"products/{product_id}")

    try:
        raw_product = product_response.json()
    except Exception:
        raw_product = {"raw": product_response.text}

    if product_response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Could not load product from the store",
                "store_response": raw_product,
            }
        )

    product = _normalize_store_product(raw_product)
    name = product.get("name") or f"מוצר {product_id}"
    price = product.get("price")
    permalink = product.get("permalink") or SHOP_BASE_URL

    caption_lines = [f"*{name}*"]

    if price:
        caption_lines.append(f"מחיר: {price}")

    caption_lines.append(permalink)
    default_caption = "\n".join(caption_lines)
    caption = (
        str(custom_caption).strip()
        if custom_caption is not None and str(custom_caption).strip()
        else default_caption
    )

    contact = db.query(Contact).filter(
        Contact.phone == phone
    ).first()

    if not contact:
        contact = Contact(phone=phone)
        db.add(contact)
        db.commit()
        db.refresh(contact)

    image_url = product.get("image")
    send_response = None
    media_id = None
    media_mime = None
    media_filename = None
    message_type = "text"

    if send_image and image_url:
        try:
            image_response = requests.get(
                image_url,
                timeout=45,
                headers={"User-Agent": "Arcadia-CRM/1.0"}
            )

            if image_response.status_code < 400 and image_response.content:
                media_mime = (
                    image_response.headers.get("Content-Type")
                    or "image/jpeg"
                ).split(";")[0]

                media_filename = f"product-{product_id}"

                upload_response = upload_whatsapp_media(
                    media_filename,
                    image_response.content,
                    media_mime
                )

                if upload_response.status_code < 400:
                    upload_data = upload_response.json()
                    media_id = upload_data.get("id")

                    if media_id:
                        send_response = send_whatsapp_media(
                            phone,
                            "image",
                            media_id,
                            caption=caption,
                            filename=media_filename
                        )
                        message_type = "image"
        except Exception as error:
            print("PRODUCT IMAGE SEND ERROR:", error)

    if send_response is None:
        send_response = send_whatsapp_message(phone, caption)
        message_type = "text"
        media_id = None
        media_mime = None
        media_filename = None

    try:
        api_data = send_response.json()
    except Exception:
        api_data = {"raw": send_response.text}

    if send_response.status_code >= 400:
        raise HTTPException(
            status_code=send_response.status_code,
            detail=api_data
        )

    whatsapp_message_id = None

    if api_data.get("messages"):
        whatsapp_message_id = api_data["messages"][0].get("id")

    message = Message(
        contact_id=contact.id,
        whatsapp_message_id=whatsapp_message_id,
        direction="outgoing",
        message_type=message_type,
        body=caption,
        media_id=media_id,
        media_mime=media_mime,
        media_filename=media_filename,
        delivery_status="accepted",
        status_updated_at=datetime.utcnow()
    )

    db.add(message)
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    db.refresh(contact)

    await broadcast_event({
        "type": "new_message",
        "phone": phone,
        "contact_id": contact.id,
        "name": contact.name,
        "status": contact.status,
        "message": {
            "id": message.id,
            "direction": message.direction,
            "type": message.message_type,
            "body": message.body,
            "media_id": message.media_id,
            "media_mime": message.media_mime,
            "media_filename": message.media_filename,
            "delivery_status": message.delivery_status,
            "status_updated_at": message.status_updated_at,
            "created_at": message.created_at,
        },
        "updated_at": contact.updated_at,
    })

    return {
        "success": True,
        "product": product,
        "whatsapp": api_data
    }


@app.get("/broadcast/templates")
def broadcast_templates():
    response = get_message_templates()
    data = response.json()

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=data
        )

    templates = []

    for template in data.get("data", []):
        if template.get("status") != "APPROVED":
            continue

        body_text = ""
        body_parameter_count = 0

        for component in template.get("components", []):
            if component.get("type") == "BODY":
                body_text = component.get("text", "") or ""

                index = 1
                while f"{{{{{index}}}}}" in body_text:
                    body_parameter_count += 1
                    index += 1

        templates.append({
            "id": template.get("id"),
            "name": template.get("name"),
            "status": template.get("status"),
            "language": template.get("language"),
            "category": template.get("category"),
            "body_text": body_text,
            "body_parameter_count": body_parameter_count,
            "components": template.get("components", []),
        })

    return templates


@app.get("/broadcast/audience")
def broadcast_audience(
    db: Session = Depends(get_db)
):
    contacts = db.query(Contact).order_by(
        Contact.updated_at.desc()
    ).all()

    result = []

    for contact in contacts:
        assignment = db.query(ContactAssignment).filter(
            ContactAssignment.contact_id == contact.id
        ).first()

        tags = (
            db.query(ContactTag)
            .filter(ContactTag.contact_id == contact.id)
            .order_by(ContactTag.name.asc())
            .all()
        )

        result.append({
            "id": contact.id,
            "name": contact.name,
            "phone": contact.phone,
            "status": contact.status,
            "assignee": assignment.assignee if assignment else None,
            "tags": [tag.name for tag in tags],
        })

    return result


@app.post("/broadcast/send")
async def send_broadcast(
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()

    template_name = (data.get("template_name") or "").strip()
    language = (data.get("language") or "").strip()
    contact_ids = data.get("contact_ids") or []
    body_parameters = data.get("body_parameters") or []

    if not template_name or not language:
        raise HTTPException(
            status_code=400,
            detail="template_name and language are required"
        )

    if not isinstance(contact_ids, list) or not contact_ids:
        raise HTTPException(
            status_code=400,
            detail="Select at least one contact"
        )

    if len(contact_ids) > 500:
        raise HTTPException(
            status_code=400,
            detail="A broadcast is limited to 500 contacts per send"
        )

    contacts = (
        db.query(Contact)
        .filter(Contact.id.in_(contact_ids))
        .all()
    )

    if not contacts:
        raise HTTPException(
            status_code=404,
            detail="No contacts found"
        )

    run = BroadcastRun(
        template_name=template_name,
        language=language,
        audience_count=len(contacts),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    semaphore = asyncio.Semaphore(10)

    async def send_one(contact):
        parameters = []

        for value in body_parameters:
            rendered = str(value)
            rendered = rendered.replace(
                "{name}",
                contact.name or contact.phone
            )
            rendered = rendered.replace(
                "{phone}",
                contact.phone
            )
            parameters.append(rendered)

        async with semaphore:
            response = await asyncio.to_thread(
                send_whatsapp_template,
                contact.phone,
                template_name,
                language,
                parameters
            )

        try:
            api_data = response.json()
        except Exception:
            api_data = {
                "raw": response.text
            }

        return contact, response.status_code, api_data

    results = await asyncio.gather(
        *[send_one(contact) for contact in contacts]
    )

    deliveries = []
    success_count = 0
    failed_count = 0

    for contact, status_code, api_data in results:
        if status_code < 400:
            success_count += 1

            whatsapp_message_id = None

            if api_data.get("messages"):
                whatsapp_message_id = (
                    api_data["messages"][0].get("id")
                )

            delivery = BroadcastDelivery(
                broadcast_id=run.id,
                contact_id=contact.id,
                phone=contact.phone,
                status="sent",
                whatsapp_message_id=whatsapp_message_id,
            )

            db.add(Message(
                contact_id=contact.id,
                whatsapp_message_id=whatsapp_message_id,
                direction="outgoing",
                message_type="template",
                body=f"[תבנית: {template_name}]"
            ))

            contact.updated_at = datetime.utcnow()

            deliveries.append({
                "contact_id": contact.id,
                "name": contact.name,
                "phone": contact.phone,
                "status": "sent",
                "error": None,
            })
        else:
            failed_count += 1
            error_text = json.dumps(
                api_data,
                ensure_ascii=False
            )

            delivery = BroadcastDelivery(
                broadcast_id=run.id,
                contact_id=contact.id,
                phone=contact.phone,
                status="failed",
                error=error_text,
            )

            deliveries.append({
                "contact_id": contact.id,
                "name": contact.name,
                "phone": contact.phone,
                "status": "failed",
                "error": api_data,
            })

        db.add(delivery)

    run.success_count = success_count
    run.failed_count = failed_count
    db.commit()

    await broadcast_event({
        "type": "broadcast_completed",
        "broadcast_id": run.id,
        "success_count": success_count,
        "failed_count": failed_count,
    })

    return {
        "broadcast_id": run.id,
        "template_name": template_name,
        "audience_count": len(contacts),
        "success_count": success_count,
        "failed_count": failed_count,
        "deliveries": deliveries,
    }


@app.get("/broadcast/history")
def broadcast_history(
    db: Session = Depends(get_db)
):
    runs = (
        db.query(BroadcastRun)
        .order_by(BroadcastRun.created_at.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "id": run.id,
            "template_name": run.template_name,
            "language": run.language,
            "audience_count": run.audience_count,
            "success_count": run.success_count,
            "failed_count": run.failed_count,
            "created_at": run.created_at,
        }
        for run in runs
    ]



@app.get("/broadcast/history/{broadcast_id}/deliveries")
def broadcast_delivery_details(
    broadcast_id: int,
    db: Session = Depends(get_db)
):
    run = db.query(BroadcastRun).filter(
        BroadcastRun.id == broadcast_id
    ).first()

    if not run:
        raise HTTPException(
            status_code=404,
            detail="Broadcast not found"
        )

    deliveries = (
        db.query(BroadcastDelivery)
        .filter(BroadcastDelivery.broadcast_id == broadcast_id)
        .order_by(BroadcastDelivery.id.asc())
        .all()
    )

    return {
        "broadcast_id": run.id,
        "template_name": run.template_name,
        "language": run.language,
        "audience_count": run.audience_count,
        "success_count": run.success_count,
        "failed_count": run.failed_count,
        "created_at": run.created_at,
        "deliveries": [
            {
                "id": delivery.id,
                "contact_id": delivery.contact_id,
                "phone": delivery.phone,
                "status": delivery.status,
                "whatsapp_message_id": delivery.whatsapp_message_id,
                "error": delivery.error,
                "created_at": delivery.created_at,
            }
            for delivery in deliveries
        ],
    }
