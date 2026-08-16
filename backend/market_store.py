"""PostgreSQL persistence for market snapshots collected by Vibe-Research.

The rest of Vibe-Research remains usable without PostgreSQL.  Market-history
features are enabled by setting ``VR_DATABASE_URL``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


DATABASE_URL = os.environ.get("VR_DATABASE_URL", "").strip()


class StoreUnavailable(RuntimeError):
    pass


def enabled() -> bool:
    return bool(DATABASE_URL)


def _driver():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise StoreUnavailable("市场历史库需要 psycopg：pip install 'psycopg[binary]'") from exc
    return psycopg, dict_row


@contextmanager
def connection() -> Iterator[Any]:
    if not DATABASE_URL:
        raise StoreUnavailable("未配置 VR_DATABASE_URL，市场历史与采集功能尚未启用")
    psycopg, dict_row = _driver()
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5) as conn:
            yield conn
    except StoreUnavailable:
        raise
    except Exception as exc:
        raise StoreUnavailable(f"无法连接市场数据库：{exc}") from exc


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id BIGSERIAL PRIMARY KEY,
    dataset TEXT NOT NULL,
    source TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    trade_date DATE NOT NULL,
    batch_ts BIGINT NOT NULL,
    payload JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(dataset, source, scope, trade_date, batch_ts)
);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_lookup
    ON market_snapshots(dataset, source, scope, trade_date DESC, batch_ts DESC);

CREATE TABLE IF NOT EXISTS market_news (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    pub_time TEXT NOT NULL DEFAULT '',
    pub_ts BIGINT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_market_news_source_pub
    ON market_news(source, pub_ts DESC);
CREATE INDEX IF NOT EXISTS idx_market_news_pub ON market_news(pub_ts DESC);

CREATE TABLE IF NOT EXISTS collector_logs (
    id BIGSERIAL PRIMARY KEY,
    job TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'fail', 'skipped')),
    detail TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_collector_logs_time ON collector_logs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_collector_logs_job ON collector_logs(job, started_at DESC);
"""

SCHEMA_MIGRATIONS = {
    2: """
CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date DATE PRIMARY KEY,
    is_open BOOLEAN NOT NULL,
    source TEXT NOT NULL DEFAULT 'sina',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stock_universe (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL DEFAULT 'A',
    active BOOLEAN NOT NULL DEFAULT true,
    source TEXT NOT NULL DEFAULT 'akshare',
    first_seen DATE NOT NULL DEFAULT CURRENT_DATE,
    last_seen DATE NOT NULL DEFAULT CURRENT_DATE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stock_universe_active ON stock_universe(active, code);

CREATE TABLE IF NOT EXISTS stock_data_snapshots (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL,
    data_type TEXT NOT NULL,
    trade_date DATE NOT NULL,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'vibe',
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(code, data_type, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_stock_data_latest
    ON stock_data_snapshots(code, data_type, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_stock_data_date
    ON stock_data_snapshots(data_type, trade_date DESC);

CREATE TABLE IF NOT EXISTS stock_collection_runs (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','ok','partial','fail')),
    total_stocks INTEGER NOT NULL DEFAULT 0,
    total_tasks INTEGER NOT NULL DEFAULT 0,
    completed_tasks INTEGER NOT NULL DEFAULT 0,
    failed_tasks INTEGER NOT NULL DEFAULT 0,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_stock_collection_runs_date
    ON stock_collection_runs(trade_date DESC, started_at DESC);

CREATE TABLE IF NOT EXISTS stock_collection_state (
    code TEXT NOT NULL,
    data_type TEXT NOT NULL,
    trade_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok','fail')),
    error TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(code, data_type, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_stock_collection_state_date
    ON stock_collection_state(trade_date, status, data_type);
""",
    3: """
UPDATE market_snapshots
SET metadata=(metadata - 'migrated_from') || '{"schema":"daily-review-v1"}'::jsonb
WHERE dataset='daily_review';
DELETE FROM market_snapshots WHERE dataset='activity';
DELETE FROM collector_logs WHERE job='activity';
""",
}


def init_db() -> None:
    with connection() as conn:
        conn.execute(SCHEMA)
        conn.execute("INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING")
        applied = {int(row["version"]) for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, sql in sorted(SCHEMA_MIGRATIONS.items()):
            if version in applied:
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))
    recover_stale_runs()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@contextmanager
def advisory_lock(name: str) -> Iterator[bool]:
    """Hold a PostgreSQL session lock so only one process runs a collector."""
    digest = hashlib.sha256(f"vibe-research:{name}".encode()).digest()[:8]
    key = int.from_bytes(digest, "big", signed=True)
    with connection() as conn:
        conn.autocommit = True
        locked = bool(conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (key,)).fetchone()["ok"])
        try:
            yield locked
        finally:
            if locked:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))


def schema_version() -> int:
    with connection() as conn:
        row = conn.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
    return int(row["version"] or 0)


def sync_trade_calendar(open_dates: list[str | date], start: date, end: date,
                        source: str = "sina") -> int:
    opened = {str(value)[:10] for value in open_dates}
    count = 0
    with connection() as conn:
        conn.execute("DELETE FROM trade_calendar WHERE source=%s AND trade_date>%s", (source, end))
        current = start
        while current <= end:
            conn.execute(
                """INSERT INTO trade_calendar(trade_date,is_open,source) VALUES (%s,%s,%s)
                   ON CONFLICT(trade_date) DO UPDATE SET is_open=excluded.is_open,
                     source=excluded.source,updated_at=now()""",
                (current, str(current) in opened, source),
            )
            count += 1
            current += timedelta(days=1)
    return count


def is_trading_day(day: str | date) -> bool | None:
    with connection() as conn:
        row = conn.execute("SELECT is_open FROM trade_calendar WHERE trade_date=%s", (day,)).fetchone()
    return bool(row["is_open"]) if row else None


def previous_trading_day(day: str | date) -> str | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT trade_date FROM trade_calendar WHERE trade_date<=%s AND is_open ORDER BY trade_date DESC LIMIT 1",
            (day,),
        ).fetchone()
    return str(row["trade_date"]) if row else None


def sync_stock_universe(rows: list[dict], trade_date: str | date,
                        source: str = "akshare") -> int:
    seen: set[str] = set()
    with connection() as conn:
        for item in rows:
            code = str(item.get("code") or item.get("代码") or "").zfill(6)
            if len(code) != 6 or not code.isdigit():
                continue
            name = str(item.get("name") or item.get("名称") or "")
            conn.execute(
                """INSERT INTO stock_universe(code,name,source,first_seen,last_seen,active,metadata)
                   VALUES (%s,%s,%s,%s,%s,true,%s::jsonb)
                   ON CONFLICT(code) DO UPDATE SET name=excluded.name,source=excluded.source,
                     last_seen=excluded.last_seen,active=true,metadata=excluded.metadata,updated_at=now()""",
                (code, name, source, trade_date, trade_date, _json(item)),
            )
            seen.add(code)
        if seen:
            conn.execute("UPDATE stock_universe SET active=false,updated_at=now() WHERE NOT (code=ANY(%s))", (list(seen),))
    return len(seen)


def stock_codes(active_only: bool = True, limit: int = 0) -> list[dict]:
    where = " WHERE active" if active_only else ""
    params: list[Any] = []
    suffix = " ORDER BY code"
    if limit > 0:
        suffix += " LIMIT %s"
        params.append(limit)
    with connection() as conn:
        rows = conn.execute("SELECT code,name,active,last_seen FROM stock_universe" + where + suffix, params).fetchall()
    return [dict(row) for row in rows]


def query_stocks(search: str = "", page: int = 1, size: int = 100) -> dict:
    clauses, params = ["active"], []
    if search:
        clauses.append("(code ILIKE %s OR name ILIKE %s)")
        value = f"%{search}%"
        params.extend([value, value])
    page, size = max(1, page), max(1, min(size, 500))
    where = " WHERE " + " AND ".join(clauses)
    with connection() as conn:
        total = int(conn.execute("SELECT count(*) AS count FROM stock_universe" + where, params).fetchone()["count"])
        rows = conn.execute(
            "SELECT code,name,market,last_seen,updated_at FROM stock_universe" + where +
            " ORDER BY code LIMIT %s OFFSET %s", [*params, size, (page - 1) * size],
        ).fetchall()
    return {"total": total, "page": page, "size": size, "items": [dict(row) for row in rows]}


def save_stock_bundle(code: str, trade_date: str | date, payloads: dict[str, Any],
                      errors: dict[str, str] | None = None, source: str = "vibe") -> None:
    errors = errors or {}
    with connection() as conn:
        for data_type, payload in payloads.items():
            raw = _json(payload)
            payload_hash = hashlib.sha256(raw.encode()).hexdigest()
            conn.execute(
                """INSERT INTO stock_data_snapshots(code,data_type,trade_date,payload,payload_hash,source)
                   VALUES (%s,%s,%s,%s::jsonb,%s,%s)
                   ON CONFLICT(code,data_type,trade_date) DO UPDATE SET payload=excluded.payload,
                     payload_hash=excluded.payload_hash,source=excluded.source,collected_at=now()""",
                (code, data_type, trade_date, raw, payload_hash, source),
            )
            conn.execute(
                """INSERT INTO stock_collection_state(code,data_type,trade_date,status,error)
                   VALUES (%s,%s,%s,'ok','') ON CONFLICT(code,data_type,trade_date)
                   DO UPDATE SET status='ok',error='',updated_at=now()""",
                (code, data_type, trade_date),
            )
        for data_type, error in errors.items():
            conn.execute(
                """INSERT INTO stock_collection_state(code,data_type,trade_date,status,error)
                   VALUES (%s,%s,%s,'fail',%s) ON CONFLICT(code,data_type,trade_date)
                   DO UPDATE SET status='fail',error=excluded.error,updated_at=now()""",
                (code, data_type, trade_date, str(error)[:1000]),
            )


def save_stock_quotes(trade_date: str | date, quotes: dict[str, dict],
                      missing: list[str] | None = None) -> None:
    """Persist one Tencent quote chunk in one transaction."""
    with connection() as conn:
        for code, payload in quotes.items():
            raw = _json(payload)
            payload_hash = hashlib.sha256(raw.encode()).hexdigest()
            conn.execute(
                """INSERT INTO stock_data_snapshots(code,data_type,trade_date,payload,payload_hash,source)
                   VALUES (%s,'quote',%s,%s::jsonb,%s,'tencent')
                   ON CONFLICT(code,data_type,trade_date) DO UPDATE SET payload=excluded.payload,
                     payload_hash=excluded.payload_hash,source=excluded.source,collected_at=now()""",
                (code, trade_date, raw, payload_hash),
            )
            conn.execute(
                """INSERT INTO stock_collection_state(code,data_type,trade_date,status,error)
                   VALUES (%s,'quote',%s,'ok','') ON CONFLICT(code,data_type,trade_date)
                   DO UPDATE SET status='ok',error='',updated_at=now()""",
                (code, trade_date),
            )
        for code in missing or []:
            conn.execute(
                """INSERT INTO stock_collection_state(code,data_type,trade_date,status,error)
                   VALUES (%s,'quote',%s,'fail','行情为空') ON CONFLICT(code,data_type,trade_date)
                   DO UPDATE SET status='fail',error='行情为空',updated_at=now()""",
                (code, trade_date),
            )


def save_stock_dataset_batch(trade_date: str | date, data_type: str,
                             payloads: dict[str, Any], missing: list[str] | None = None,
                             source: str = "vibe", missing_error: str = "数据为空") -> None:
    """Persist one all-market dataset in a single transaction."""
    with connection() as conn:
        for code, payload in payloads.items():
            raw = _json(payload)
            payload_hash = hashlib.sha256(raw.encode()).hexdigest()
            conn.execute(
                """INSERT INTO stock_data_snapshots(code,data_type,trade_date,payload,payload_hash,source)
                   VALUES (%s,%s,%s,%s::jsonb,%s,%s)
                   ON CONFLICT(code,data_type,trade_date) DO UPDATE SET payload=excluded.payload,
                     payload_hash=excluded.payload_hash,source=excluded.source,collected_at=now()""",
                (code, data_type, trade_date, raw, payload_hash, source),
            )
            conn.execute(
                """INSERT INTO stock_collection_state(code,data_type,trade_date,status,error)
                   VALUES (%s,%s,%s,'ok','') ON CONFLICT(code,data_type,trade_date)
                   DO UPDATE SET status='ok',error='',updated_at=now()""",
                (code, data_type, trade_date),
            )
        for code in missing or []:
            conn.execute(
                """INSERT INTO stock_collection_state(code,data_type,trade_date,status,error)
                   VALUES (%s,%s,%s,'fail',%s) ON CONFLICT(code,data_type,trade_date)
                   DO UPDATE SET status='fail',error=excluded.error,updated_at=now()""",
                (code, data_type, trade_date, missing_error),
            )


def latest_stock_data(code: str, data_type: str, trade_date: str | None = None) -> dict | None:
    params: list[Any] = [code, data_type]
    clause = ""
    if trade_date:
        clause = " AND trade_date=%s"
        params.append(trade_date)
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM stock_data_snapshots WHERE code=%s AND data_type=%s" + clause +
            " ORDER BY trade_date DESC LIMIT 1", params,
        ).fetchone()
    return dict(row) if row else None


def stock_data_history(code: str, data_type: str = "", limit: int = 100) -> list[dict]:
    params: list[Any] = [code]
    clause = ""
    if data_type:
        clause = " AND data_type=%s"
        params.append(data_type)
    params.append(max(1, min(limit, 500)))
    with connection() as conn:
        rows = conn.execute(
            "SELECT code,data_type,trade_date,payload,source,collected_at FROM stock_data_snapshots "
            "WHERE code=%s" + clause + " ORDER BY trade_date DESC,data_type LIMIT %s", params,
        ).fetchall()
    return [dict(row) for row in rows]


def stock_data_dates(code: str) -> list[str]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM stock_data_snapshots WHERE code=%s ORDER BY trade_date DESC",
            (code,),
        ).fetchall()
    return [str(row["trade_date"]) for row in rows]


def stock_data_bundle(code: str, trade_date: str | date) -> dict:
    with connection() as conn:
        rows = conn.execute(
            """SELECT data_type,payload,collected_at FROM stock_data_snapshots
               WHERE code=%s AND trade_date=%s ORDER BY data_type""",
            (code, trade_date),
        ).fetchall()
    return {"code": code, "trade_date": str(trade_date),
            "data": {row["data_type"]: row["payload"] for row in rows},
            "collected_at": max((str(row["collected_at"]) for row in rows), default="")}


def stock_data_done(code: str, data_type: str, trade_date: str | date) -> bool:
    with connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM stock_collection_state WHERE code=%s AND data_type=%s AND trade_date=%s AND status='ok'",
            (code, data_type, trade_date),
        ).fetchone()
    return bool(row)


def stock_data_done_set(trade_date: str | date, data_types: list[str]) -> set[tuple[str, str]]:
    if not data_types:
        return set()
    with connection() as conn:
        rows = conn.execute(
            """SELECT code,data_type FROM stock_collection_state
               WHERE trade_date=%s AND status='ok' AND data_type=ANY(%s)""",
            (trade_date, data_types),
        ).fetchall()
    return {(row["code"], row["data_type"]) for row in rows}


def start_stock_run(trade_date: str | date, total_stocks: int, total_tasks: int) -> int:
    with connection() as conn:
        row = conn.execute(
            """INSERT INTO stock_collection_runs(trade_date,status,total_stocks,total_tasks)
               VALUES (%s,'running',%s,%s) RETURNING id""",
            (trade_date, total_stocks, total_tasks),
        ).fetchone()
    return int(row["id"])


def update_stock_run(run_id: int, completed: int, failed: int,
                     status: str | None = None, detail: dict | None = None) -> None:
    with connection() as conn:
        if status:
            conn.execute(
                """UPDATE stock_collection_runs SET completed_tasks=%s,failed_tasks=%s,status=%s,
                   detail=%s::jsonb,finished_at=now() WHERE id=%s""",
                (completed, failed, status, _json(detail or {}), run_id),
            )
        else:
            conn.execute(
                "UPDATE stock_collection_runs SET completed_tasks=%s,failed_tasks=%s,detail=%s::jsonb WHERE id=%s",
                (completed, failed, _json(detail or {}), run_id),
            )


def stock_run_status() -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM stock_collection_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def recover_stale_runs(hours: int = 6) -> int:
    with connection() as conn:
        cur = conn.execute(
            """UPDATE stock_collection_runs SET status='fail',finished_at=now(),
               detail=detail || '{"recovered":"stale running task"}'::jsonb
               WHERE status='running' AND started_at < now() - (%s * interval '1 hour')""",
            (max(1, hours),),
        )
    return cur.rowcount


def failed_stock_tasks(trade_date: str | date, limit: int = 500) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """SELECT code,data_type,error,updated_at FROM stock_collection_state
               WHERE trade_date=%s AND status='fail' ORDER BY updated_at,code,data_type LIMIT %s""",
            (trade_date, max(1, min(limit, 5000))),
        ).fetchall()
    return [dict(row) for row in rows]


def data_health(trade_date: str | date | None = None) -> dict:
    target = str(trade_date or previous_trading_day(date.today()) or date.today())
    with connection() as conn:
        universe = int(conn.execute("SELECT count(*) AS count FROM stock_universe WHERE active").fetchone()["count"])
        datasets = conn.execute(
            """SELECT dataset,source,count(*) AS snapshots,min(trade_date) AS first_date,
                      max(trade_date) AS last_date,max(collected_at) AS latest_collect
               FROM market_snapshots GROUP BY dataset,source ORDER BY dataset,source"""
        ).fetchall()
        coverage = conn.execute(
            """SELECT types.data_type,
                      count(*) FILTER (WHERE state.status='ok') AS success,
                      count(*) FILTER (WHERE state.status='fail') AS failed
               FROM (SELECT unnest(%s::text[]) AS data_type) types
               LEFT JOIN stock_collection_state state ON state.data_type=types.data_type
                    AND state.trade_date=%s
               GROUP BY types.data_type ORDER BY types.data_type""",
            (list(("quote", "valuation", "reports", "percentile", "financials", "announcements",
                   "news", "margin", "block_trade", "holders", "dividend", "fund_flow",
                   "dragon_tiger", "lockup", "blocks", "hot_concepts", "investor_qa")), target),
        ).fetchall()
        jobs = conn.execute(
            """SELECT DISTINCT ON (job) job,source,status,detail,started_at,finished_at
               FROM collector_logs ORDER BY job,started_at DESC,id DESC"""
        ).fetchall()
        failures = conn.execute(
            """SELECT code,data_type,error,updated_at FROM stock_collection_state
               WHERE trade_date=%s AND status='fail'
               ORDER BY updated_at DESC,code,data_type LIMIT 100""",
            (target,),
        ).fetchall()
        run = conn.execute("SELECT * FROM stock_collection_runs ORDER BY id DESC LIMIT 1").fetchone()
    stock_coverage = []
    for row in coverage:
        item = dict(row)
        item["success"] = int(item["success"] or 0)
        item["failed"] = int(item["failed"] or 0)
        item["missing"] = max(universe - item["success"] - item["failed"], 0)
        item["coverage_pct"] = round(item["success"] / max(universe, 1) * 100, 2)
        stock_coverage.append(item)
    return {"trade_date": target, "schema_version": schema_version(), "stock_universe": universe,
            "stock_coverage": stock_coverage, "datasets": [dict(row) for row in datasets],
            "jobs": [dict(row) for row in jobs], "stock_failures": [dict(row) for row in failures],
            "stock_run": dict(run) if run else None}


def save_snapshot(dataset: str, source: str, scope: str, trade_date: str | date,
                  batch_ts: int, payload: Any, metadata: dict | None = None) -> None:
    with connection() as conn:
        conn.execute(
            """INSERT INTO market_snapshots
               (dataset, source, scope, trade_date, batch_ts, payload, metadata)
               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
               ON CONFLICT(dataset, source, scope, trade_date, batch_ts) DO UPDATE SET
                 payload=excluded.payload, metadata=excluded.metadata, collected_at=now()""",
            (dataset, source, scope or "", trade_date, int(batch_ts),
             _json(payload), _json(metadata or {})),
        )


def update_daily_review_global_indices(trade_date: str | date, rows: list[dict]) -> bool:
    """Replace only the global-index section of an existing daily-review snapshot."""
    with connection() as conn:
        updated = conn.execute(
            """UPDATE market_snapshots
               SET payload=jsonb_set(payload,'{global_indices}',%s::jsonb,true),
                   metadata=metadata || '{"global_indices_source":"public-history-v1"}'::jsonb
               WHERE dataset='daily_review' AND source='vibe' AND scope=''
                 AND trade_date=%s AND batch_ts=0""",
            (_json(rows), trade_date),
        )
    return updated.rowcount == 1


def upsert_news(items: list[dict]) -> int:
    """Persist normalized news and return the number of newly inserted rows."""
    if not items:
        return 0
    added = 0
    with connection() as conn:
        for item in items:
            row = conn.execute(
                """INSERT INTO market_news
                   (source,title,summary,link,pub_time,pub_ts,content_hash,collected_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s))
                   ON CONFLICT(content_hash) DO NOTHING
                   RETURNING id""",
                (item["source"], item.get("title") or "", item.get("summary") or "",
                 item.get("link") or "", item.get("pub_time") or "", int(item.get("pub_ts") or 0),
                 item["content_hash"], int(item.get("created_at") or datetime.now().timestamp())),
            ).fetchone()
            added += int(bool(row))
    return added


def query_news(source: str = "all", limit: int = 100, from_date: str = "",
               to_date: str = "") -> dict:
    clauses, params = [], []
    if source and source != "all":
        clauses.append("source=%s")
        params.append(source)
    cn = ZoneInfo("Asia/Shanghai")
    if from_date:
        clauses.append("pub_ts>=%s")
        params.append(int(datetime.combine(date.fromisoformat(from_date), dt_time.min, cn).timestamp()))
    if to_date:
        clauses.append("pub_ts<=%s")
        params.append(int(datetime.combine(date.fromisoformat(to_date), dt_time.max, cn).timestamp()))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    size = max(1, min(limit, 500))
    with connection() as conn:
        total = conn.execute("SELECT count(*) AS c FROM market_news" + where, params).fetchone()["c"]
        rows = conn.execute(
            "SELECT source,title,summary,link,pub_time,pub_ts,collected_at "
            "FROM market_news" + where + " ORDER BY pub_ts DESC,id DESC LIMIT %s",
            [*params, size],
        ).fetchall()
        stats = conn.execute(
            "SELECT source,count(*) AS count,max(pub_ts) AS latest FROM market_news GROUP BY source ORDER BY source"
        ).fetchall()
    return {"total": total, "items": [dict(row) for row in rows],
            "sources": [dict(row) for row in stats]}


def news_dates(source: str = "all") -> list[str]:
    where, params = "", []
    if source and source != "all":
        where, params = " WHERE source=%s", [source]
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT to_char(to_timestamp(pub_ts) AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS day "
            "FROM market_news" + where + " ORDER BY day DESC LIMIT 500",
            params,
        ).fetchall()
    return [row["day"] for row in rows]


def latest_snapshot(dataset: str, source: str = "", scope: str = "",
                    trade_date: str | None = None) -> dict | None:
    clauses, params = ["dataset=%s"], [dataset]
    if source:
        clauses.append("source=%s")
        params.append(source)
    if scope:
        clauses.append("scope=%s")
        params.append(scope)
    if trade_date:
        clauses.append("trade_date=%s")
        params.append(trade_date)
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM market_snapshots WHERE " + " AND ".join(clauses) +
            " ORDER BY trade_date DESC, batch_ts DESC LIMIT 1", params,
        ).fetchone()
        return dict(row) if row else None


def snapshots(dataset: str, source: str = "", scope: str = "",
              trade_date: str | None = None, limit: int = 100) -> list[dict]:
    clauses, params = ["dataset=%s"], [dataset]
    for col, value in (("source", source), ("scope", scope), ("trade_date", trade_date)):
        if value:
            clauses.append(f"{col}=%s")
            params.append(value)
    params.append(max(1, min(limit, 500)))
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM market_snapshots WHERE " + " AND ".join(clauses) +
            " ORDER BY trade_date DESC, batch_ts DESC LIMIT %s", params,
        ).fetchall()
        return [dict(row) for row in rows]


def available_dates(dataset: str, source: str = "", scope: str = "") -> list[str]:
    clauses, params = ["dataset=%s"], [dataset]
    if source:
        clauses.append("source=%s")
        params.append(source)
    if scope:
        clauses.append("scope=%s")
        params.append(scope)
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM market_snapshots WHERE " + " AND ".join(clauses) +
            " ORDER BY trade_date DESC LIMIT 500", params,
        ).fetchall()
        return [str(r["trade_date"]) for r in rows]


def start_log(job: str, source: str = "") -> int:
    with connection() as conn:
        row = conn.execute(
            "INSERT INTO collector_logs(job,source,status) VALUES (%s,%s,'running') RETURNING id",
            (job, source),
        ).fetchone()
        return int(row["id"])


def finish_log(log_id: int, status: str, detail: str = "") -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE collector_logs SET status=%s,detail=%s,finished_at=now() WHERE id=%s",
            (status, detail[:1000], log_id),
        )


def logs(job: str = "", status: str = "", page: int = 1, size: int = 100) -> dict:
    clauses, params = [], []
    if job:
        clauses.append("job=%s")
        params.append(job)
    if status:
        clauses.append("status=%s")
        params.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    page, size = max(page, 1), max(1, min(size, 200))
    with connection() as conn:
        total = conn.execute("SELECT count(*) AS c FROM collector_logs" + where, params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM collector_logs" + where + " ORDER BY started_at DESC,id DESC LIMIT %s OFFSET %s",
            [*params, size, (page - 1) * size],
        ).fetchall()
        return {"total": total, "page": page, "size": size, "items": [dict(r) for r in rows]}


def status_summary() -> list[dict]:
    with connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT ON (job) job, source, status, detail, started_at, finished_at
            FROM collector_logs ORDER BY job, started_at DESC, id DESC
        """).fetchall()
        return [dict(r) for r in rows]


def job_succeeded_since(job: str, since: datetime) -> bool:
    with connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM collector_logs WHERE job=%s AND status='ok' AND started_at>=%s LIMIT 1",
            (job, since),
        ).fetchone()
        return bool(row)


def prune(dataset: str, before: date) -> int:
    with connection() as conn:
        cur = conn.execute("DELETE FROM market_snapshots WHERE dataset=%s AND trade_date<%s", (dataset, before))
        return cur.rowcount


def import_news_sqlite(path: str, dry_run: bool = False) -> dict[str, int]:
    """Import only the deduplicated news feed from a legacy SQLite database."""
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise ValueError(f"SQLite 文件不存在：{src}")
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT source,title,summary,link,pub_time,pub_ts,content_hash,created_at FROM news ORDER BY id"
        )]
    finally:
        conn.close()
    return {"news": len(rows), "news_added": 0 if dry_run else upsert_news(rows)}


def import_panel_sqlite(path: str, dry_run: bool = False) -> dict[str, int]:
    """Import a legacy market SQLite database without modifying the source file."""
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise ValueError(f"SQLite 文件不存在：{src}")
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    mappings = {
        "news": ("news_feed", "source", "''", "date(pub_ts, 'unixepoch', 'localtime')", "pub_ts"),
        "hot_list": ("hot", "platform", "''", "substr(snapshot_time,1,10)", "batch_ts"),
        "fund_flow_series": ("fund_flow", "source", "scope", "trade_date", "ts"),
        "fund_flow_daily": ("fund_flow_daily", "source", "scope || ':' || period", "trade_date", "0"),
        "concept_quote": ("boards", "source", "CASE WHEN is_daily=1 THEN 'daily' ELSE 'live' END", "trade_date", "ts"),
        "market_pool": ("market_pool", "'eastmoney'", "pool", "trade_date", "0"),
        "tech_rank": ("tech_rank", "'ths'", "indicator || ':' || param", "trade_date", "0"),
        "lhb": ("lhb", "'eastmoney'", "''", "trade_date", "0"),
        "lhb_detail": ("lhb_detail", "'eastmoney'", "code", "trade_date", "0"),
        "stock_list": ("stocks", "'akshare'", "''", "date(updated_at, 'unixepoch', 'localtime')", "0"),
        "concept_cons": ("board_constituents", "'eastmoney'", "board", "date(updated_at, 'unixepoch', 'localtime')", "0"),
    }
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, (dataset, source_expr, scope_expr, date_expr, batch_expr) in mappings.items():
            if table not in tables:
                continue
            rows = conn.execute(
                f"SELECT {source_expr} source, {scope_expr} scope, "
                f"{date_expr} trade_date, {batch_expr} batch_ts, * FROM {table} ORDER BY rowid"
            ).fetchall()
            groups: dict[tuple, list] = {}
            for row in rows:
                d = dict(row)
                key = (d.pop("source"), d.pop("scope", ""), d.pop("trade_date"), int(d.pop("batch_ts") or 0))
                groups.setdefault(key, []).append(d)
            counts[table] = len(rows)
            if not dry_run:
                if table == "news":
                    upsert_news([dict(row) for row in rows])
                with connection() as target:
                    for (source, scope, trade_date, batch_ts), payload in groups.items():
                        if not trade_date:
                            continue
                        target.execute(
                            """INSERT INTO market_snapshots
                               (dataset,source,scope,trade_date,batch_ts,payload,metadata)
                               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                               ON CONFLICT(dataset,source,scope,trade_date,batch_ts) DO UPDATE SET
                                 payload=excluded.payload,metadata=excluded.metadata,collected_at=now()""",
                            (dataset, str(source), str(scope or ""), str(trade_date), batch_ts,
                             _json(payload), _json({"imported_from": str(src), "source_table": table})),
                        )
        if "concept_board" in tables:
            rows = [dict(r) for r in conn.execute("SELECT * FROM concept_board ORDER BY source,board")]
            counts["concept_board"] = len(rows)
            if not dry_run:
                snapshot_day = str(date.fromtimestamp(src.stat().st_mtime))
                for source in sorted({str(r.get("source") or "") for r in rows}):
                    save_snapshot("boards", source, "catalog", snapshot_day, 0,
                                  [r for r in rows if str(r.get("source") or "") == source],
                                  {"imported_from": str(src), "source_table": "concept_board"})
        if "collect_log" in tables:
            rows = conn.execute("SELECT ts,category,method,detail,status FROM collect_log ORDER BY id").fetchall()
            counts["collect_log"] = len(rows)
            if not dry_run:
                with connection() as target:
                    for row in rows:
                        d = dict(row)
                        target.execute(
                            """INSERT INTO collector_logs(job,source,status,detail,started_at,finished_at)
                               VALUES (%s,%s,%s,%s,to_timestamp(%s),to_timestamp(%s))""",
                            (d["category"], d.get("method") or "", d["status"], d.get("detail") or "",
                             d["ts"], d["ts"]),
                        )
    finally:
        conn.close()
    return counts


def import_panel_sqlite_date(path: str, target_date: str, dry_run: bool = False) -> dict[str, int]:
    """Import one calendar day's legacy rows without replaying the full database."""
    selected_date = date.fromisoformat(target_date)
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise ValueError(f"SQLite 文件不存在：{src}")
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    mappings = {
        "hot_list": ("hot", "platform", "''", "substr(snapshot_time,1,10)", "batch_ts"),
        "fund_flow_series": ("fund_flow", "source", "scope", "trade_date", "ts"),
        "fund_flow_daily": ("fund_flow_daily", "source", "scope || ':' || period", "trade_date", "0"),
        "concept_quote": ("boards", "source", "CASE WHEN is_daily=1 THEN 'daily' ELSE 'live' END", "trade_date", "ts"),
        "market_pool": ("market_pool", "'eastmoney'", "pool", "trade_date", "0"),
        "tech_rank": ("tech_rank", "'ths'", "indicator || ':' || param", "trade_date", "0"),
        "lhb": ("lhb", "'eastmoney'", "''", "trade_date", "0"),
        "lhb_detail": ("lhb_detail", "'eastmoney'", "code", "trade_date", "0"),
        "stock_list": ("stocks", "'akshare'", "''", "date(updated_at, 'unixepoch', 'localtime')", "0"),
        "concept_cons": ("board_constituents", "'eastmoney'", "board", "date(updated_at, 'unixepoch', 'localtime')", "0"),
    }
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "news" in tables:
            news = [dict(row) for row in conn.execute(
                "SELECT source,title,summary,link,pub_time,pub_ts,content_hash,created_at FROM news "
                "WHERE date(pub_ts, 'unixepoch', 'localtime')=? ORDER BY id", (str(selected_date),),
            )]
            counts["news"] = len(news)
            counts["news_added"] = 0 if dry_run else upsert_news(news)
        for table, (dataset, source_expr, scope_expr, date_expr, batch_expr) in mappings.items():
            if table not in tables:
                continue
            rows = conn.execute(
                f"SELECT {source_expr} source, {scope_expr} scope, {date_expr} trade_date, "
                f"{batch_expr} batch_ts, * FROM {table} WHERE {date_expr}=? ORDER BY rowid",
                (str(selected_date),),
            ).fetchall()
            counts[table] = len(rows)
            groups: dict[tuple, list] = {}
            for row in rows:
                payload = dict(row)
                key = (payload.pop("source"), payload.pop("scope", ""),
                       payload.pop("trade_date"), int(payload.pop("batch_ts") or 0))
                groups.setdefault(key, []).append(payload)
            if dry_run:
                continue
            with connection() as target:
                for (source, scope, trade_day, batch_ts), payload in groups.items():
                    target.execute(
                        """INSERT INTO market_snapshots
                           (dataset,source,scope,trade_date,batch_ts,payload,metadata)
                           VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                           ON CONFLICT(dataset,source,scope,trade_date,batch_ts) DO UPDATE SET
                             payload=excluded.payload,metadata=excluded.metadata,collected_at=now()""",
                        (dataset, str(source), str(scope or ""), str(trade_day), batch_ts,
                         _json(payload), _json({"imported_from": str(src), "source_table": table})),
                    )
    finally:
        conn.close()
    return counts
