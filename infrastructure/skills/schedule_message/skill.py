from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from infrastructure.skills.base import SkillBase, SkillContext, SkillResult


class ScheduleMessageSkill(SkillBase):
    id = "schedule_message"
    cmd_name = "SCHEDULE_MESSAGE"
    display = {"en": "Schedule Message", "ru": "Запланировать сообщение"}
    description = {
        "en": "AI schedules a message to be sent later.",
        "ru": "AI планирует сообщение, которое будет отправлено позже.",
    }
    example = "[SCHEDULE_MESSAGE: 2026-03-16 09:00 | message text]"
    action_type = "post"
    persist_in_db = False
    parse_re = re.compile(r"\[SCHEDULE[_ ]MESSAGE:\s*(.*?)\]", re.DOTALL | re.IGNORECASE)
    _prompt_dir = Path(__file__).resolve().parent

    async def execute(self, match: re.Match, ctx: SkillContext) -> SkillResult:
        """Not used directly — schedule_message is batch-processed after the stream.

        See ``execute_batch`` for the actual implementation.
        """
        return SkillResult()

    async def execute_batch(
        self,
        matches: list[re.Match],
        ctx: SkillContext,
    ) -> None:
        """Create autonomy tasks for all SCHEDULE_MESSAGE matches."""
        if not matches:
            return

        try:
            from infrastructure.autonomy.task_queue import create_task, cancel_duplicate_scheduled
            from infrastructure.database.models import TriggerType
            from infrastructure.clock import local_to_utc

            for m in matches:
                raw_arg = m.group(1).strip()
                if "|" not in raw_arg:
                    continue
                ts_str, sched_msg = raw_arg.split("|", 1)
                sched_msg = sched_msg.strip()
                if not sched_msg:
                    continue
                try:
                    local_dt = datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M")
                    scheduled_at = local_to_utc(local_dt)
                    await cancel_duplicate_scheduled(ctx.db, ctx.account_id, scheduled_at, "chat")
                    payload = json.dumps({"message": sched_msg, "source": "chat"})
                    await create_task(
                        ctx.db,
                        account_id=ctx.account_id,
                        trigger_type=TriggerType.TIME,
                        payload=payload,
                        scheduled_at=scheduled_at,
                    )
                    ctx.logger.info("[schedule_message] created task at %s: %s", ts_str.strip(), sched_msg[:60])
                except ValueError:
                    ctx.logger.warning("[schedule_message] bad timestamp: %r", ts_str)
        except Exception as exc:
            ctx.logger.warning("[schedule_message] processing failed: %s", exc)


skill = ScheduleMessageSkill()
