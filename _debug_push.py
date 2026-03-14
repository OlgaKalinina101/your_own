import asyncio, sys, json
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding='utf-8')

async def debug():
    from infrastructure.database.engine import get_db_session
    from infrastructure.database.models.autonomy_task import AutonomyTask, TaskStatus, TriggerType
    from sqlalchemy import select

    now = datetime.now(timezone.utc)
    print(f"Current UTC: {now}")

    async with get_db_session() as db:
        # All tasks
        r = await db.execute(select(AutonomyTask).where(AutonomyTask.account_id == "default"))
        all_tasks = list(r.scalars().all())
        print(f"Total tasks: {len(all_tasks)}")
        for t in all_tasks:
            p = json.loads(t.payload) if t.payload else {}
            print(f"  id={t.id} status={t.status} trigger={t.trigger_type}")
            print(f"  scheduled_at={t.scheduled_at} (type={type(t.scheduled_at).__name__})")
            print(f"  is_due={t.scheduled_at <= now if t.scheduled_at else 'N/A'}")
            print(f"  message: {p.get('message', '')[:60]}")
            print()

        # Try the exact query from get_due_tasks
        r2 = await db.execute(
            select(AutonomyTask).where(
                AutonomyTask.account_id == "default",
                AutonomyTask.trigger_type == TriggerType.TIME,
                AutonomyTask.status == TaskStatus.PENDING,
                AutonomyTask.scheduled_at <= now,
            )
        )
        due = list(r2.scalars().all())
        print(f"Due tasks (get_due_tasks query): {len(due)}")

asyncio.run(debug())
