from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    status = Column(String, default="ליד חדש")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship(
        "Message",
        back_populates="contact",
        cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)

    whatsapp_message_id = Column(String, unique=True, index=True, nullable=True)
    direction = Column(String, nullable=False)  # incoming / outgoing
    message_type = Column(String, default="text")
    body = Column(Text, nullable=True)
    media_id = Column(String, nullable=True, index=True)
    media_mime = Column(String, nullable=True)
    media_filename = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    contact = relationship(
        "Contact",
        back_populates="messages"
    )


class ConversationState(Base):
    __tablename__ = "conversation_states"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(
        Integer,
        ForeignKey("contacts.id"),
        unique=True,
        nullable=False,
        index=True
    )
    unread_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class ContactNote(Base):
    __tablename__ = "contact_notes"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ContactTag(Base):
    __tablename__ = "contact_tags"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)



class ContactAssignment(Base):
    __tablename__ = "contact_assignments"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(
        Integer,
        ForeignKey("contacts.id"),
        unique=True,
        nullable=False,
        index=True
    )
    assignee = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FollowUpReminder(Base):
    __tablename__ = "follow_up_reminders"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    note = Column(Text, nullable=False)
    due_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)



class BroadcastRun(Base):
    __tablename__ = "broadcast_runs"

    id = Column(Integer, primary_key=True, index=True)
    template_name = Column(String, nullable=False)
    language = Column(String, nullable=False)
    audience_count = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BroadcastDelivery(Base):
    __tablename__ = "broadcast_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    broadcast_id = Column(
        Integer,
        ForeignKey("broadcast_runs.id"),
        nullable=False,
        index=True
    )
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    phone = Column(String, nullable=False)
    status = Column(String, nullable=False)
    whatsapp_message_id = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
