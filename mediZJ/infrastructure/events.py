"""执行终态和对应事件在同一 MySQL 事务提交。"""

import json


async def terminate(conn, run_id: str, status: str, error: str) -> bool:
    row = (
        await conn.execute(
            "SELECT seq,status FROM chat_runs WHERE run_id=%s FOR UPDATE", (run_id,)
        )
    ).fetchone()
    if row is None or row["status"] in {"completed", "failed", "cancelled", "expired"}:
        return False
    seq = row["seq"] + 1
    await conn.execute(
        "UPDATE chat_runs SET status=%s,error=%s,seq=%s,updated_at=UTC_TIMESTAMP(6) "
        "WHERE run_id=%s",
        (status, error, seq, run_id),
    )
    await conn.execute(
        "UPDATE questionnaires SET status='expired' WHERE run_id=%s AND status='pending'",
        (run_id,),
    )
    await conn.execute(
        "INSERT INTO run_events VALUES (%s,%s,'error',%s,UTC_TIMESTAMP(6))",
        (
            run_id,
            seq,
            json.dumps({"error": error, "status": status}, ensure_ascii=False),
        ),
    )
    return True
