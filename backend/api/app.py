import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sanic import Sanic
from sanic.response import json as sanic_json

from db import create_pool, ensure_schema, seed_if_empty
from rules import (
    MSG_BASE_TEMP_INVALID,
    MSG_BASE_TEMP_REQUIRED,
    MSG_SPAN_NOT_CONFIGURED,
    MSG_TEMP_INVALID,
    MSG_TEMP_REQUIRED,
    compensate_reading,
    finite_number,
    judge_microstrain,
    validate_coefficient,
    validate_temperature,
)

SECRET = os.environ.get("JWT_SECRET", "bridge-strain-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "surveyor": {"role": "writer", "password_hash": pwd.hash("surv123456")},
    "reviewer": {"role": "reader", "password_hash": pwd.hash("rev123456")},
}

app = Sanic("bridge-strain-shift")


def _auth_header(request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def _require_user(request) -> dict | None:
    return _decode_user(_auth_header(request))


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _err(detail: str, status: int = 400):
    # 所有入参错误统一走这里：网页与绕过网页的请求拿到的说法完全一致。
    return sanic_json({"detail": detail}, status=status)


@app.before_server_start
async def setup(_app, _loop):
    pool = await create_pool()
    _app.ctx.pool = pool
    await ensure_schema(pool)
    await seed_if_empty(pool)


@app.after_server_stop
async def teardown(_app, _loop):
    pool = _app.ctx.pool
    if pool:
        await pool.close()


@app.get("/api/health")
async def health(_request):
    return sanic_json({"status": "ok", "service": "bridge-strain-shift"})


@app.post("/api/auth/login")
async def login(request):
    body = request.json or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return _err("用户名或密码错误", status=401)
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return sanic_json(
        {"access_token": token, "username": username, "role": user["role"]}
    )


@app.get("/api/readings")
async def list_readings(request):
    if not _require_user(request):
        return _err("未登录", status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT r.id, r.span_code, r.microstrain,
                       r.field_temperature, r.coefficient, r.base_temperature,
                       r.compensated_microstrain,
                       r.verdict, r.reason, r.status,
                       r.created_by, r.created_at, r.processed_at,
                       l.id IS NOT NULL AS in_ledger
                FROM strain_readings r
                LEFT JOIN compensation_ledger l ON l.reading_id = r.id
                ORDER BY r.id DESC
                """
            )
            rows = await cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "span_code": r["span_code"],
                "microstrain": r["microstrain"],
                "field_temperature": r["field_temperature"],
                "coefficient": r["coefficient"],
                "base_temperature": r["base_temperature"],
                "compensated_microstrain": r["compensated_microstrain"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "in_ledger": r["in_ledger"],
                "created_by": r["created_by"],
                "created_at": _iso(r["created_at"]),
                "processed_at": _iso(r["processed_at"]),
            }
        )
    return sanic_json(out)


@app.get("/api/compensation")
async def list_compensation(request):
    if not _require_user(request):
        return _err("未登录", status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT span_code, coefficient, base_temperature,
                       updated_by, updated_at
                FROM span_compensation
                ORDER BY span_code
                """
            )
            rows = await cur.fetchall()
    return sanic_json(
        [
            {
                "span_code": r["span_code"],
                "coefficient": r["coefficient"],
                "base_temperature": r["base_temperature"],
                "updated_by": r["updated_by"],
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ]
    )


@app.post("/api/compensation")
async def upsert_compensation(request):
    user = _require_user(request)
    if not user:
        return _err("未登录", status=401)
    if user["role"] != "writer":
        return _err("仅测量员可修改温度补偿设置", status=403)

    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return _err("跨段编号不能为空")

    coefficient, detail = validate_coefficient(body.get("coefficient"))
    if detail:
        return _err(detail)
    base_temperature, detail = validate_temperature(
        body.get("base_temperature"),
        required_msg=MSG_BASE_TEMP_REQUIRED,
        invalid_msg=MSG_BASE_TEMP_INVALID,
    )
    if detail:
        return _err(detail)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO span_compensation
                    (span_code, coefficient, base_temperature, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (span_code) DO UPDATE
                SET coefficient = EXCLUDED.coefficient,
                    base_temperature = EXCLUDED.base_temperature,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                RETURNING span_code, coefficient, base_temperature,
                          updated_by, updated_at
                """,
                (span_code, coefficient, base_temperature, user["username"]),
            )
            row = await cur.fetchone()
        await conn.commit()

    return sanic_json(
        {
            "span_code": row["span_code"],
            "coefficient": row["coefficient"],
            "base_temperature": row["base_temperature"],
            "updated_by": row["updated_by"],
            "updated_at": _iso(row["updated_at"]),
            "message": "温度补偿设置已保存（不影响已落账记录）",
        }
    )


@app.post("/api/readings")
async def create_reading(request):
    user = _require_user(request)
    if not user:
        return _err("未登录", status=401)
    if user["role"] != "writer":
        return _err("仅测量员可提交应变读数", status=403)

    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return _err("跨段编号不能为空")

    microstrain = finite_number(body.get("microstrain"))
    if microstrain is None:
        return _err("微应变必须是数字")

    # 判定前先吃温度补偿：现场气温必带，且跨段必须已设补偿系数与基准气温。
    field_temperature, detail = validate_temperature(
        body.get("field_temperature"),
        required_msg=MSG_TEMP_REQUIRED,
        invalid_msg=MSG_TEMP_INVALID,
    )
    if detail:
        return _err(detail)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT coefficient, base_temperature
                FROM span_compensation
                WHERE span_code = %s
                """,
                (span_code,),
            )
            setting = await cur.fetchone()
            if not setting:
                return _err(MSG_SPAN_NOT_CONFIGURED)

            coefficient = float(setting["coefficient"])
            base_temperature = float(setting["base_temperature"])
            compensated = compensate_reading(
                microstrain, coefficient, field_temperature, base_temperature
            )
            verdict, reason = judge_microstrain(compensated)

            # 入队与账本落笔必须同一次落库：同一个事务里先插在线单再写账本，
            # 任一步失败整体回滚，不可能出现“入了队却没账本”或反之。
            try:
                async with conn.transaction():
                    await cur.execute(
                        """
                        INSERT INTO strain_readings
                            (span_code, microstrain, field_temperature, coefficient,
                             base_temperature, compensated_microstrain,
                             verdict, reason, status, created_by, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                'pending', %s, now())
                        RETURNING id, created_at
                        """,
                        (
                            span_code,
                            microstrain,
                            field_temperature,
                            coefficient,
                            base_temperature,
                            compensated,
                            verdict,
                            reason,
                            user["username"],
                        ),
                    )
                    row = await cur.fetchone()
                    await cur.execute(
                        """
                        INSERT INTO compensation_ledger
                            (reading_id, span_code, raw_microstrain, field_temperature,
                             coefficient, base_temperature, compensated_microstrain,
                             verdict, reason, recorded_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["id"],
                            span_code,
                            microstrain,
                            field_temperature,
                            coefficient,
                            base_temperature,
                            compensated,
                            verdict,
                            reason,
                            user["username"],
                        ),
                    )
                    reading_id = row["id"]
                    created_at = row["created_at"]
            except Exception:
                # 事务已回滚：队列与账本同生共死，缺一边即整体失败。
                raise

    return sanic_json(
        {
            "id": reading_id,
            "span_code": span_code,
            "microstrain": microstrain,
            "field_temperature": field_temperature,
            "coefficient": coefficient,
            "base_temperature": base_temperature,
            "compensated_microstrain": compensated,
            "verdict": verdict,
            "reason": reason,
            "status": "pending",
            "created_by": user["username"],
            "created_at": _iso(created_at),
            "processed_at": None,
            "message": "已入队并写入补偿账本，后台工人将认领并发布结论",
        },
        status=201,
    )


@app.get("/api/ledger")
async def list_ledger(request):
    if not _require_user(request):
        return _err("未登录", status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 账本旧值与在线单现值逐字段对拍：在线单被改过即报“不一致”。
            await cur.execute(
                """
                SELECT l.id, l.reading_id, l.span_code,
                       l.raw_microstrain, l.field_temperature,
                       l.coefficient, l.base_temperature,
                       l.compensated_microstrain, l.verdict, l.reason,
                       l.recorded_by, l.recorded_at,
                       (r.id IS NOT NULL) AS online_exists,
                       r.microstrain AS online_raw,
                       r.compensated_microstrain AS online_compensated,
                       r.verdict AS online_verdict,
                       (r.id IS NOT NULL
                        AND r.microstrain = l.raw_microstrain
                        AND r.compensated_microstrain
                            IS NOT DISTINCT FROM l.compensated_microstrain
                        AND r.verdict = l.verdict) AS values_match
                FROM compensation_ledger l
                LEFT JOIN strain_readings r ON r.id = l.reading_id
                ORDER BY l.id DESC
                """
            )
            rows = await cur.fetchall()

    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "reading_id": r["reading_id"],
                "span_code": r["span_code"],
                "raw_microstrain": r["raw_microstrain"],
                "field_temperature": r["field_temperature"],
                "coefficient": r["coefficient"],
                "base_temperature": r["base_temperature"],
                "compensated_microstrain": r["compensated_microstrain"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "recorded_by": r["recorded_by"],
                "recorded_at": _iso(r["recorded_at"]),
                "online_exists": r["online_exists"],
                "online_raw": r["online_raw"],
                "online_compensated": r["online_compensated"],
                "online_verdict": r["online_verdict"],
                "values_match": r["values_match"],
            }
        )
    return sanic_json(out)
