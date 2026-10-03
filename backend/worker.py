"""后台工人：用 SKIP LOCKED 认领 pending 应变读数并写入合格/越界结论。

温度补偿在报送时已由服务端算好，并随补偿账本一同落库；
工人只负责把账本里冻结的结论回填到在线单，不重新计算。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def claim_one(conn):
    with conn.transaction():
        row = conn.execute(
            """
            SELECT r.id, r.compensated_microstrain
            FROM strain_readings r
            WHERE r.status = 'pending'
            ORDER BY r.id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE strain_readings SET status = 'processing' WHERE id = %s",
            (row["id"],),
        )
        return row


def finish(conn, reading_id: int) -> None:
    row = conn.execute(
        """
        SELECT verdict, reason, compensated_microstrain
        FROM compensation_ledger
        WHERE reading_id = %s
        """,
        (reading_id,),
    ).fetchone()
    if row is None:
        # 理论上不会发生：入队与落账同事务，缺账即视为异常并重排队
        raise RuntimeError(f"reading {reading_id} 缺少补偿账本记录")
    conn.execute(
        """
        UPDATE strain_readings
        SET status = 'done', verdict = %s, reason = %s,
            compensated_microstrain = %s, processed_at = now()
        WHERE id = %s
        """,
        (
            row["verdict"],
            row["reason"],
            row["compensated_microstrain"],
            reading_id,
        ),
    )
    conn.commit()


def run_once(conn) -> bool:
    row = claim_one(conn)
    if not row:
        return False
    try:
        finish(conn, row["id"])
    except Exception:
        conn.execute(
            "UPDATE strain_readings SET status = 'pending' WHERE id = %s",
            (row["id"],),
        )
        conn.commit()
        raise
    return True


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        seed_if_empty_sync(conn)
        conn.commit()

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
