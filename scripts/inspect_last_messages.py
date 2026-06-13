"""Read-only: find rows that look like failed/error replies + show the latest pair."""
import asyncio

from sqlalchemy import select, or_

from infrastructure.database.engine import get_db_session
from infrastructure.database.models.message import Message


async def main() -> None:
    async with get_db_session() as session:
        print("=== rows containing an error marker ===")
        err = (
            await session.execute(
                select(Message)
                .where(
                    or_(
                        Message.text.ilike("%OpenRouter error%"),
                        Message.text.ilike("%error 404%"),
                        Message.text.ilike("%[OpenRouter%"),
                    )
                )
                .order_by(Message.created_at.desc())
            )
        ).scalars().all()
        if not err:
            print("(none found)")
        for r in err:
            snippet = (r.text or "").replace("\n", " ")[:100]
            print(
                f"{r.created_at.isoformat()} | acct={r.account_id} | pair={r.pair_id} "
                f"| {r.role:9} | kind={r.message_kind:9} | src={r.source:6} | {snippet!r}"
            )

        print("\n=== assistant rows for the latest pair(s) ===")
        latest = (
            await session.execute(
                select(Message)
                .where(Message.role == "assistant")
                .order_by(Message.created_at.desc())
                .limit(5)
            )
        ).scalars().all()
        for r in latest:
            snippet = (r.text or "").replace("\n", " ")[:100]
            print(
                f"{r.created_at.isoformat()} | pair={r.pair_id} | kind={r.message_kind:9} "
                f"| src={r.source:6} | {snippet!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())
