"""One-off: delete a single message pair by pair_id (prints rows removed)."""
import asyncio
import sys

from sqlalchemy import select

from infrastructure.database.engine import get_db_session
from infrastructure.database.models.message import Message
from infrastructure.database.repositories.message_repo import MessageRepository

PAIR_ID = sys.argv[1] if len(sys.argv) > 1 else ""


async def main() -> None:
    if not PAIR_ID:
        print("usage: python scripts/delete_pair.py <pair_id>")
        return

    async with get_db_session() as session:
        before = (
            await session.execute(
                select(Message).where(Message.pair_id == PAIR_ID)
            )
        ).scalars().all()
        print(f"Found {len(before)} rows for pair {PAIR_ID}:")
        for r in before:
            snippet = (r.text or "").replace("\n", " ")[:60]
            print(f"  {r.role:9} kind={r.message_kind:9} src={r.source:6} {snippet!r}")

        deleted = await MessageRepository(session).delete_pair(PAIR_ID)
        print(f"\nDeleted {deleted} rows.")


if __name__ == "__main__":
    asyncio.run(main())
