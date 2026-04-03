"""Autonomy task queue — scheduled and manual tasks stored in PostgreSQL.

Each task is a row in ``autonomy_tasks``.  The scheduled-push worker polls
this table every 60 seconds and dispatches tasks whose ``scheduled_at`` has
passed.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

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
        created_at=datetime.now(),
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


async def get_recent_tasks(
    db: AsyncSession,
    account_id: str,
    hours: int = 12,
) -> list[AutonomyTask]:
    """Return PENDING, DONE and CANCELLED TIME tasks scheduled within the last N hours."""
    cutoff = datetime.now() - timedelta(hours=hours)
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.trigger_type == TriggerType.TIME,
            AutonomyTask.scheduled_at >= cutoff,
            AutonomyTask.status.in_([TaskStatus.PENDING, TaskStatus.DONE, TaskStatus.CANCELLED]),
        ).order_by(AutonomyTask.scheduled_at.asc())
    )
    return list(result.scalars().all())


async def cancel_task_by_time(
    db: AsyncSession,
    account_id: str,
    scheduled_at: datetime,
) -> bool:
    """Cancel a PENDING task at the given local time. Returns True if found."""
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.trigger_type == TriggerType.TIME,
            AutonomyTask.status == TaskStatus.PENDING,
            AutonomyTask.scheduled_at == scheduled_at,
        )
    )
    tasks = list(result.scalars().all())
    for t in tasks:
        t.status = TaskStatus.CANCELLED
    if tasks:
        await db.commit()
    return bool(tasks)


async def reschedule_task(
    db: AsyncSession,
    account_id: str,
    old_scheduled_at: datetime,
    new_scheduled_at: datetime,
) -> bool:
    """Move a PENDING task to a new time. Returns True if found."""
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.trigger_type == TriggerType.TIME,
            AutonomyTask.status == TaskStatus.PENDING,
            AutonomyTask.scheduled_at == old_scheduled_at,
        )
    )
    tasks = list(result.scalars().all())
    for t in tasks:
        t.scheduled_at = new_scheduled_at
    if tasks:
        await db.commit()
    return bool(tasks)


async def rewrite_task(
    db: AsyncSession,
    account_id: str,
    scheduled_at: datetime,
    new_text: str,
) -> bool:
    """Replace the message payload of a PENDING task. Returns True if found."""
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.trigger_type == TriggerType.TIME,
            AutonomyTask.status == TaskStatus.PENDING,
            AutonomyTask.scheduled_at == scheduled_at,
        )
    )
    tasks = list(result.scalars().all())
    for t in tasks:
        try:
            import json
            pd = json.loads(t.payload) if t.payload else {}
            pd["message"] = new_text
            t.payload = json.dumps(pd, ensure_ascii=False)
        except Exception:
            t.payload = json.dumps({"message": new_text}, ensure_ascii=False)
    if tasks:
        await db.commit()
    return bool(tasks)


async def get_due_tasks(db: AsyncSession, account_id: str) -> list[AutonomyTask]:
    """Return PENDING TIME-triggered tasks whose scheduled_at <= now (local server time)."""
    now = datetime.now()
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.trigger_type == TriggerType.TIME,
            AutonomyTask.status == TaskStatus.PENDING,
            AutonomyTask.scheduled_at <= now,
        )
    )
    return list(result.scalars().all())


async def update_task_payload_message(db: AsyncSession, task_id: str, new_text: str) -> None:
    """Replace the ``message`` field inside the payload of any task (any status).

    Used by the push validator to persist the rewritten text before delivery.
    """
    import json as _json
    result = await db.execute(select(AutonomyTask).where(AutonomyTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        return
    try:
        pd = _json.loads(task.payload) if task.payload else {}
        pd["message"] = new_text
        task.payload = _json.dumps(pd, ensure_ascii=False)
    except Exception:
        task.payload = _json.dumps({"message": new_text}, ensure_ascii=False)
    await db.commit()


async def mark_done(db: AsyncSession, task_id: str) -> None:
    await db.execute(
        update(AutonomyTask)
        .where(AutonomyTask.id == task_id)
        .values(status=TaskStatus.DONE, completed_at=datetime.now())
    )
    await db.commit()


async def cancel_duplicate_scheduled(
    db: AsyncSession,
    account_id: str,
    scheduled_at: datetime,
    source: str,
) -> int:
    """Cancel ALL pending TIME tasks at the same scheduled time, regardless of source.

    The ``source`` parameter is kept for backwards compatibility but no longer
    used as a filter — any pending task at the same time slot is a duplicate.
    """
    result = await db.execute(
        select(AutonomyTask).where(
            AutonomyTask.account_id == account_id,
            AutonomyTask.trigger_type == TriggerType.TIME,
            AutonomyTask.status == TaskStatus.PENDING,
            AutonomyTask.scheduled_at == scheduled_at,
        )
    )
    tasks = list(result.scalars().all())
    for t in tasks:
        t.status = TaskStatus.CANCELLED
    if tasks:
        await db.commit()
        logger.debug(
            "[task_queue] cancelled %d duplicate(s) at %s before new %s task",
            len(tasks), scheduled_at, source,
        )
    return len(tasks)
