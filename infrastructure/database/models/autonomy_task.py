"""SQLAlchemy ORM model for autonomy_tasks table."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum as SAEnum, String, Text
from sqlalchemy.dialects.postgresql import UUID

from infrastructure.database.engine import Base


class TriggerType(str, enum.Enum):
    TIME   = "TIME"    # fires at scheduled_at
    MANUAL = "MANUAL"  # requires user confirmation


class TaskStatus(str, enum.Enum):
    PENDING   = "PENDING"
    DONE      = "DONE"
    CANCELLED = "CANCELLED"
    FAILED    = "FAILED"


class AutonomyTask(Base):
    __tablename__ = "autonomy_tasks"

    id           = Column(String(64), primary_key=True)
    account_id   = Column(String(128), nullable=False, index=True)
    trigger_type = Column(SAEnum(TriggerType), nullable=False)
    status       = Column(SAEnum(TaskStatus), nullable=False, default=TaskStatus.PENDING, index=True)
    payload      = Column(Text, nullable=False)          # JSON string or plain message text
    scheduled_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at   = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
