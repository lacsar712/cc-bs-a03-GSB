import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rules import compensate_reading, judge_microstrain

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54398/bridgestrain"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strain_readings (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    microstrain double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS field_temperature double precision;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS coefficient double precision;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS base_temperature double precision;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS compensated_microstrain double precision;
CREATE INDEX IF NOT EXISTS idx_strain_readings_status ON strain_readings (status, id);

-- 跨段温度补偿设置：一个跨段一行，系数与基准气温。
CREATE TABLE IF NOT EXISTS span_compensation (
    span_code text PRIMARY KEY,
    coefficient double precision NOT NULL,
    base_temperature double precision NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT span_comp_coefficient_range
        CHECK (coefficient >= 0 AND coefficient <= 1),
    CONSTRAINT span_comp_base_temperature_range
        CHECK (base_temperature >= -50 AND base_temperature <= 100)
);

-- 补偿账本：只追加。原文读数与补偿后读数在此各存一份快照，
-- 在线单（strain_readings）事后被改也动不到账本旧值。
CREATE TABLE IF NOT EXISTS compensation_ledger (
    id bigserial PRIMARY KEY,
    reading_id integer NOT NULL REFERENCES strain_readings (id),
    span_code text NOT NULL,
    raw_microstrain double precision NOT NULL,
    field_temperature double precision NOT NULL,
    coefficient double precision NOT NULL,
    base_temperature double precision NOT NULL,
    compensated_microstrain double precision NOT NULL,
    verdict text NOT NULL,
    reason text NOT NULL,
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_comp_ledger_reading ON compensation_ledger (reading_id);
-- 一单只能落一笔账，配合外键防止在线单被删除后账本成孤儿。
CREATE UNIQUE INDEX IF NOT EXISTS idx_comp_ledger_reading_unique
    ON compensation_ledger (reading_id);

CREATE OR REPLACE FUNCTION compensation_ledger_freeze() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '补偿账本只允许追加，禁止修改或删除旧记录';
END;
$$;

DROP TRIGGER IF EXISTS trg_comp_ledger_no_row_change ON compensation_ledger;
CREATE TRIGGER trg_comp_ledger_no_row_change
    BEFORE UPDATE OR DELETE ON compensation_ledger
    FOR EACH ROW EXECUTE FUNCTION compensation_ledger_freeze();

DROP TRIGGER IF EXISTS trg_comp_ledger_no_truncate ON compensation_ledger;
CREATE TRIGGER trg_comp_ledger_no_truncate
    BEFORE TRUNCATE ON compensation_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION compensation_ledger_freeze();
"""

# 种子补偿设置：系数 0.1、基准气温 20℃。
SEED_COMPENSATION = [
    ("跨中S1", 0.1, 20.0),
    ("支座S2", 0.1, 20.0),
]

# 种子读数：现场气温等于基准气温时补偿后读数等于原文。
SEED_READINGS = [
    ("跨中S1", 150.0, 20.0),
    ("支座S2", 40.0, 20.0),
]


async def create_pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        conninfo=DSN,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await pool.open()
    return pool


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.commit()


def _seed_compensation_rows(cur) -> dict:
    for span_code, coefficient, base_temperature in SEED_COMPENSATION:
        cur.execute(
            """
            INSERT INTO span_compensation
                (span_code, coefficient, base_temperature, updated_by)
            VALUES (%s, %s, %s, 'surveyor')
            ON CONFLICT (span_code) DO NOTHING
            """,
            (span_code, coefficient, base_temperature),
        )


def _seed_reading_with_ledger(cur, span_code, raw, field_temperature) -> None:
    setting = cur.execute(
        "SELECT coefficient, base_temperature FROM span_compensation WHERE span_code = %s",
        (span_code,),
    ).fetchone()
    coefficient = float(setting["coefficient"])
    base_temperature = float(setting["base_temperature"])
    compensated = compensate_reading(
        raw, coefficient, field_temperature, base_temperature
    )
    verdict, reason = judge_microstrain(compensated)
    reading = cur.execute(
        """
        INSERT INTO strain_readings
            (span_code, microstrain, field_temperature, coefficient,
             base_temperature, compensated_microstrain,
             verdict, reason, status, created_by, processed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'done', 'surveyor', now())
        RETURNING id
        """,
        (
            span_code,
            raw,
            field_temperature,
            coefficient,
            base_temperature,
            compensated,
            verdict,
            reason,
        ),
    ).fetchone()
    cur.execute(
        """
        INSERT INTO compensation_ledger
            (reading_id, span_code, raw_microstrain, field_temperature,
             coefficient, base_temperature, compensated_microstrain,
             verdict, reason, recorded_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'surveyor')
        """,
        (
            reading["id"],
            span_code,
            raw,
            field_temperature,
            coefficient,
            base_temperature,
            compensated,
            verdict,
            reason,
        ),
    )


async def seed_if_empty(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) AS n FROM strain_readings")
            row = await cur.fetchone()
            if row["n"] == 0:
                for span_code, coefficient, base_temperature in SEED_COMPENSATION:
                    await cur.execute(
                        """
                        INSERT INTO span_compensation
                            (span_code, coefficient, base_temperature, updated_by)
                        VALUES (%s, %s, %s, 'surveyor')
                        ON CONFLICT (span_code) DO NOTHING
                        """,
                        (span_code, coefficient, base_temperature),
                    )
                for span_code, raw, field_temperature in SEED_READINGS:
                    await cur.execute(
                        "SELECT coefficient, base_temperature FROM span_compensation WHERE span_code = %s",
                        (span_code,),
                    )
                    setting = await cur.fetchone()
                    coefficient = float(setting["coefficient"])
                    base_temperature = float(setting["base_temperature"])
                    compensated = compensate_reading(
                        raw, coefficient, field_temperature, base_temperature
                    )
                    verdict, reason = judge_microstrain(compensated)
                    await cur.execute(
                        """
                        INSERT INTO strain_readings
                            (span_code, microstrain, field_temperature, coefficient,
                             base_temperature, compensated_microstrain,
                             verdict, reason, status, created_by, processed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'done', 'surveyor', now())
                        RETURNING id
                        """,
                        (
                            span_code,
                            raw,
                            field_temperature,
                            coefficient,
                            base_temperature,
                            compensated,
                            verdict,
                            reason,
                        ),
                    )
                    reading = await cur.fetchone()
                    await cur.execute(
                        """
                        INSERT INTO compensation_ledger
                            (reading_id, span_code, raw_microstrain, field_temperature,
                             coefficient, base_temperature, compensated_microstrain,
                             verdict, reason, recorded_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'surveyor')
                        """,
                        (
                            reading["id"],
                            span_code,
                            raw,
                            field_temperature,
                            coefficient,
                            base_temperature,
                            compensated,
                            verdict,
                            reason,
                        ),
                    )
        await conn.commit()


def connect_sync():
    import psycopg

    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM strain_readings").fetchone()
    if row["n"] > 0:
        return
    with conn.transaction():
        _seed_compensation_rows(conn)
        for span_code, raw, field_temperature in SEED_READINGS:
            _seed_reading_with_ledger(conn, span_code, raw, field_temperature)
    conn.commit()
