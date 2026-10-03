"""后台工人：用 SKIP LOCKED 认领待处理读数并发布结论。

温度补偿与合格/越界判定在报送落库时已完成，原文、补偿后读数与结论
同步写进补偿账本。工人只负责把状态从 pending 翻到 done，绝不重算，
以免在线结论与账本旧值产生分歧。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def claim_and_finish(conn):
    with conn.transaction():
        row = conn.execute(
            """
            SELECT id
            FROM strain_readings
            WHERE status = 'pending'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """
            UPDATE strain_readings
            SET status = 'done', processed_at = now()
            WHERE id = %s
            """,
            (row["id"],),
        )
        return row["id"]


def run_once(conn) -> bool:
    try:
        reading_id = claim_and_finish(conn)
    except Exception:
        # 单事务出错整体回滚，该单保持 pending，下轮再认领。
        conn.rollback()
        raise
    conn.commit()
    return reading_id is not None


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        conn.commit()
        seed_if_empty_sync(conn)

    while True:
        try:
            with connect_sync() as conn:
                processed = run_once(conn)
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
            processed = False
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
