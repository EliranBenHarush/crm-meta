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

    created_at = Column(DateTime, default=datetime.utcnow)

    contact = relationship(
        "Contact",
        back_populates="messages"
    )