import asyncio
import json
import os

from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime

from database import Base, engine, SessionLocal
from models import Contact, Message, ConversationState, ContactNote, ContactTag, ContactAssignment, FollowUpReminder, BroadcastRun, BroadcastDelivery
from whatsapp import send_whatsapp_message, get_message_templates, send_whatsapp_template


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Arcadia CRM API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "arcadia_crm_verify_2026")

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
            "unread_count": (
                conversation_state.unread_count
                if conversation_state else 0
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
