"""Persisted AI evidence reviews and durable, deduplicated email delivery state."""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class WorkspaceIntelligence(Base):
    __tablename__ = "workspace_intelligence"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("demo_users.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    model: Mapped[str] = mapped_column(String(100))
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailPreference(Base):
    __tablename__ = "email_preferences"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("demo_users.id", ondelete="CASCADE"), primary_key=True)
    recipient: Mapped[str] = mapped_column(String(320))
    reports_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailDelivery(Base):
    __tablename__ = "email_deliveries"
    delivery_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("demo_users.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
