"""Autonomy task queue — scheduled and manual tasks stored in PostgreSQL.

Each task is a row in ``autonomy_tasks``.  The scheduled-push worker polls
this table every 60 seconds and dispatches tasks whose ``scheduled_at`` has
passed.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database.models.autonomy_task import AutonomyTask, TaskStatus, TriggerType

logger = logging.getLogger("autonomy.task_queue")


async def create_task(
    db: AsyncSession,
    *,
    account_id: str,
    trigger_type: TriggerType,
    payload: str,
    scheduled_at: datetime | None = None,
) -> AutonomyTask:
    task = AutonomyTask(
        id=str(uuid.uuid4()),
        account_id=account_id,
        trigger_type=trigger_type,
        payload=payload,
        scheduled_at=scheduled_at,
        status=TaskStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.debug("[task_queue] created task id=%s type=%s", task.id, trigger_type)
    return task


async def get_pending_tasks(db: AsyncSession, account_id: str) -> list[AutonomyTask]:
    """Return all PENDING tasks for the account (regardless of scheduled_at)."""
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.status == TaskStatus.PENDING,
        ).order_by(AutonomyTask.scheduled_at.asc().nullslast())
    )
    return list(result.scalars().all())


async def get_due_tasks(db: AsyncSession, account_id: str) -> list[AutonomyTask]:
    """Return PENDING TIME-triggered tasks whose scheduled_at <= now."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.trigger_type == TriggerType.TIME,
            AutonomyTask.status == TaskStatus.PENDING,
            AutonomyTask.scheduled_at <= now,
        )
    )
    return list(result.scalars().all())


async def mark_done(db: AsyncSession, task_id: str) -> None:
    await db.execute(
        update(AutonomyTask)
        .where(AutonomyTask.id == task_id)
        .values(status=TaskStatus.DONE, completed_at=datetime.now(timezone.utc))
    )
    await db.commit()


async def cancel_duplicate_scheduled(
    db: AsyncSession,
    account_id: str,
    scheduled_at: datetime,
    source: str,
) -> int:
    """Cancel pending TIME tasks at the same time from the same source."""
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.trigger_type == TriggerType.TIME,
            AutonomyTask.status == TaskStatus.PENDING,
            AutonomyTask.scheduled_at == scheduled_at,
            AutonomyTask.payload.contains(source),
        )
    )
    tasks = list(result.scalars().all())
    for t in tasks:
        t.status = TaskStatus.CANCELLED
    if tasks:
        await db.commit()
    return len(tasks)
