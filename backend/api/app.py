import math
import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sanic import Sanic
from sanic.response import json as sanic_json

from db import create_pool, ensure_schema, seed_if_empty
from rules import (
    COEFF_MAX,
    COEFF_MIN,
    TEMP_MAX,
    TEMP_MIN,
    compensate,
    judge_compensated,
    validate_coeff,
    validate_temp,
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


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not math.isfinite(number):
        return None
    return number


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
        return sanic_json({"detail": "用户名或密码错误"}, status=401)
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return sanic_json(
        {"access_token": token, "username": username, "role": user["role"]}
    )


def _reading_out(r) -> dict:
    return {
        "id": r["id"],
        "span_code": r["span_code"],
        "microstrain": r["microstrain"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "site_temp": r.get("site_temp"),
        "base_temp": r.get("base_temp"),
        "coeff": r.get("coeff"),
        "compensated_microstrain": r.get("compensated_microstrain"),
        "amended": r.get("amended", False),
        "amended_at": _iso(r.get("amended_at")),
        "created_by": r["created_by"],
        "created_at": _iso(r["created_at"]),
        "processed_at": _iso(r["processed_at"]),
    }


READING_COLS = """
    id, span_code, microstrain, verdict, reason, status,
    site_temp, base_temp, coeff, compensated_microstrain,
    amended, amended_at, created_by, created_at, processed_at
"""


@app.get("/api/readings")
async def list_readings(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {READING_COLS} FROM strain_readings ORDER BY id DESC"
            )
            rows = await cur.fetchall()
    return sanic_json([_reading_out(r) for r in rows])


@app.post("/api/readings")
async def create_reading(request):
    """报送读数：服务端先做温度补偿再判定；入队与账本落笔同一事务。"""
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可提交应变读数"}, status=403)

    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)

    microstrain = _finite_number(body.get("microstrain"))
    if microstrain is None:
        return sanic_json({"detail": "微应变必须是数字"}, status=400)

    # 缺气温直接退回——网页与直连 API 同一条校验、同一句说法
    if body.get("site_temp") is None or body.get("site_temp") == "":
        return sanic_json({"detail": "现场气温不能为空"}, status=400)
    site_temp = validate_temp(body.get("site_temp"))
    if site_temp is None:
        return sanic_json(
            {"detail": f"现场气温必须是 {TEMP_MIN:g}～{TEMP_MAX:g} ℃ 之间的数字"},
            status=400,
        )

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        try:
            async with conn.cursor() as cur:
                # 锁定该跨段补偿行，保证取系数、入队、落账期间系数不被改动
                await cur.execute(
                    """
                    SELECT span_code, coeff, base_temp
                    FROM compensation_config
                    WHERE span_code = %s
                    FOR UPDATE
                    """,
                    (span_code,),
                )
                cfg = await cur.fetchone()
                if cfg is None:
                    await conn.rollback()
                    return sanic_json(
                        {
                            "detail": (
                                f"跨段「{span_code}」未设置温度补偿，"
                                "请先在温度补偿专页设置补偿系数与基准气温"
                            )
                        },
                        status=400,
                    )

                coeff = validate_coeff(cfg["coeff"])
                base_temp = validate_temp(cfg["base_temp"])
                if coeff is None:
                    await conn.rollback()
                    return sanic_json(
                        {
                            "detail": (
                                f"补偿系数越出允许范围（{COEFF_MIN:g}～"
                                f"{COEFF_MAX:g}），请重新设置后再报送"
                            )
                        },
                        status=400,
                    )
                if base_temp is None:
                    await conn.rollback()
                    return sanic_json(
                        {
                            "detail": (
                                f"基准气温必须是 {TEMP_MIN:g}～{TEMP_MAX:g} ℃ "
                                "之间的数字，请重新设置后再报送"
                            )
                        },
                        status=400,
                    )

                compensated = compensate(
                    microstrain, coeff, site_temp, base_temp
                )
                verdict, reason = judge_compensated(compensated)

                # 入队
                await cur.execute(
                    """
                    INSERT INTO strain_readings
                        (span_code, microstrain, status, created_by, created_at,
                         site_temp, base_temp, coeff, compensated_microstrain)
                    VALUES (%s, %s, 'pending', %s, now(), %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        span_code,
                        microstrain,
                        user["username"],
                        site_temp,
                        base_temp,
                        coeff,
                        compensated,
                    ),
                )
                reading_id = (await cur.fetchone())["id"]

                # 账本落笔——与入队同一事务，任一失败整体回滚
                await cur.execute(
                    """
                    INSERT INTO compensation_ledger
                        (reading_id, span_code, raw_microstrain, coeff,
                         base_temp, site_temp, compensated_microstrain,
                         verdict, reason, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        reading_id,
                        span_code,
                        microstrain,
                        coeff,
                        base_temp,
                        site_temp,
                        compensated,
                        verdict,
                        reason,
                        user["username"],
                    ),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    return sanic_json(
        {
            "id": reading_id,
            "span_code": span_code,
            "microstrain": microstrain,
            "site_temp": site_temp,
            "base_temp": base_temp,
            "coeff": coeff,
            "compensated_microstrain": compensated,
            "verdict": verdict,
            "reason": reason,
            "status": "pending",
            "message": (
                f"已入队并写入补偿账本：原文 {microstrain:g} → "
                f"补偿后 {compensated:g} με，预判 {verdict}"
            ),
        },
        status=201,
    )


@app.post("/api/readings/<reading_id:int>/amend")
async def amend_reading(request, reading_id: int):
    """修订在线单据上的数字（模拟事后改单）。账本由触发器保护，旧值不动；
    账本与在线单的差异由 /api/ledger 对拍暴露。"""
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可修订应变读数"}, status=403)

    body = request.json or {}
    microstrain = _finite_number(body.get("microstrain"))
    if microstrain is None:
        return sanic_json({"detail": "微应变必须是数字"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {READING_COLS} FROM strain_readings WHERE id = %s FOR UPDATE",
                (reading_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return sanic_json({"detail": "单据不存在"}, status=404)
            if row["status"] != "done":
                return sanic_json(
                    {"detail": "仅已判定的单据可修订"}, status=400
                )
            if row["coeff"] is None:
                return sanic_json(
                    {"detail": "该单据早于温度补偿上线，无补偿参数，不可修订"},
                    status=400,
                )

            compensated = compensate(
                microstrain,
                float(row["coeff"]),
                float(row["site_temp"]),
                float(row["base_temp"]),
            )
            verdict, reason = judge_compensated(compensated)
            await cur.execute(
                """
                UPDATE strain_readings
                SET microstrain = %s, compensated_microstrain = %s,
                    verdict = %s, reason = %s,
                    amended = true, amended_at = now()
                WHERE id = %s
                """,
                (microstrain, compensated, verdict, reason, reading_id),
            )
            await cur.execute(
                f"SELECT {READING_COLS} FROM strain_readings WHERE id = %s",
                (reading_id,),
            )
            updated = await cur.fetchone()
        await conn.commit()

    return sanic_json(_reading_out(updated))


@app.get("/api/compensation/config")
async def list_config(request):
    """补偿系数表：测量员、复核员均可查看。"""
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT span_code, coeff, base_temp, updated_by,
                       created_at, updated_at
                FROM compensation_config
                ORDER BY span_code
                """
            )
            rows = await cur.fetchall()
    return sanic_json(
        [
            {
                "span_code": r["span_code"],
                "coeff": r["coeff"],
                "base_temp": r["base_temp"],
                "updated_by": r["updated_by"],
                "created_at": _iso(r["created_at"]),
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ]
    )


@app.put("/api/compensation/config")
async def upsert_config(request):
    """设置/更新跨段补偿系数与基准气温。仅测量员；系数越界一律退回。"""
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可设置补偿系数，复核员只读"}, status=403)

    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)

    if body.get("coeff") is None or body.get("coeff") == "":
        return sanic_json({"detail": "补偿系数不能为空"}, status=400)
    coeff = validate_coeff(body.get("coeff"))
    if coeff is None:
        return sanic_json(
            {
                "detail": (
                    f"补偿系数越出允许范围（{COEFF_MIN:g}～{COEFF_MAX:g}，"
                    "含端点），请重新输入"
                )
            },
            status=400,
        )

    if body.get("base_temp") is None or body.get("base_temp") == "":
        return sanic_json({"detail": "基准气温不能为空"}, status=400)
    base_temp = validate_temp(body.get("base_temp"))
    if base_temp is None:
        return sanic_json(
            {
                "detail": (
                    f"基准气温必须是 {TEMP_MIN:g}～{TEMP_MAX:g} ℃ 之间的数字"
                )
            },
            status=400,
        )

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO compensation_config
                    (span_code, coeff, base_temp, updated_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (span_code) DO UPDATE
                SET coeff = EXCLUDED.coeff,
                    base_temp = EXCLUDED.base_temp,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                RETURNING span_code, coeff, base_temp, updated_by,
                          created_at, updated_at
                """,
                (span_code, coeff, base_temp, user["username"]),
            )
            row = await cur.fetchone()
        await conn.commit()

    return sanic_json(
        {
            "span_code": row["span_code"],
            "coeff": row["coeff"],
            "base_temp": row["base_temp"],
            "updated_by": row["updated_by"],
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "message": f"已保存跨段「{span_code}」补偿系数 {coeff:g}、基准气温 {base_temp:g} ℃",
        }
    )


def _float_close(a, b, eps: float = 1e-9) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= eps


@app.get("/api/ledger")
async def list_ledger(request):
    """补偿账本：追加式、不可改；每条与在线单对拍，标出漂移。"""
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT l.id AS ledger_id, l.reading_id, l.span_code,
                       l.raw_microstrain, l.coeff, l.base_temp, l.site_temp,
                       l.compensated_microstrain, l.verdict, l.reason,
                       l.created_by AS ledger_by, l.created_at AS ledger_at,
                       r.microstrain AS online_raw,
                       r.compensated_microstrain AS online_compensated,
                       r.verdict AS online_verdict, r.status AS online_status
                FROM compensation_ledger l
                LEFT JOIN strain_readings r ON r.id = l.reading_id
                ORDER BY l.id DESC
                """
            )
            rows = await cur.fetchall()

    out = []
    for r in rows:
        diffs = []
        if r["online_status"] is None:
            state = "missing"
            diffs.append("在线单不存在")
        elif r["online_status"] != "done":
            state = "pending"
        else:
            if not _float_close(r["raw_microstrain"], r["online_raw"]):
                diffs.append(
                    f"原文不一致：账本 {r['raw_microstrain']:g} / "
                    f"在线 {r['online_raw']:g}"
                )
            if not _float_close(
                r["compensated_microstrain"], r["online_compensated"]
            ):
                diffs.append(
                    "补偿后读数不一致：账本 "
                    f"{r['compensated_microstrain']:g} / "
                    f"在线 {r['online_compensated']:g}"
                )
            if r["verdict"] != r["online_verdict"]:
                diffs.append(
                    f"结论不一致：账本 {r['verdict']} / "
                    f"在线 {r['online_verdict']}"
                )
            state = "match" if not diffs else "drift"

        out.append(
            {
                "ledger_id": r["ledger_id"],
                "reading_id": r["reading_id"],
                "span_code": r["span_code"],
                "raw_microstrain": r["raw_microstrain"],
                "coeff": r["coeff"],
                "base_temp": r["base_temp"],
                "site_temp": r["site_temp"],
                "compensated_microstrain": r["compensated_microstrain"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "ledger_by": r["ledger_by"],
                "ledger_at": _iso(r["ledger_at"]),
                "online_status": r["online_status"],
                "reconcile_state": state,
                "diffs": diffs,
            }
        )
    return sanic_json(out)
