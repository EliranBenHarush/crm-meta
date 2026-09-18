from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from datetime import datetime

from database import Base, engine, SessionLocal
from models import Contact, Message
from whatsapp import send_whatsapp_message


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Arcadia CRM API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


VERIFY_TOKEN = "arcadia_crm_verify_2026"


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@app.get("/")
def home():
    return {
        "status": "ok",
        "app": "Arcadia CRM"
    }


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

        messages = value.get("messages", [])

        if not messages:
            return {"status": "ignored"}

        message = messages[0]

        phone = message.get("from")
        whatsapp_message_id = message.get("id")
        message_type = message.get("type", "unknown")

        body = None

        if message_type == "text":
            body = message.get("text", {}).get("body")

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
            body=body
        )

        db.add(new_message)
        db.commit()

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

        result.append({
            "id": contact.id,
            "name": contact.name,
            "phone": contact.phone,
            "status": contact.status,
            "last_message": (
                last_message.body if last_message else None
            ),
            "updated_at": contact.updated_at
        })

    return result


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
            "created_at": message.created_at
        }
        for message in messages
    ]


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
        body=text
    )

    db.add(message)

    contact.updated_at = datetime.utcnow()

    db.commit()

    return {
        "success": True,
        "whatsapp": api_data
    }