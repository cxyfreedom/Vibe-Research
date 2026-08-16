"""PostgreSQL-backed market data collectors.

Each collector performs one bounded fetch and writes an immutable PostgreSQL
snapshot. Scheduling lives in ``market_runtime`` so these functions are also
safe to invoke manually and in live tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import market_store as store


CN = ZoneInfo("Asia/Shanghai")
NEWS_SOURCE_TIMEOUT = float(os.environ.get("VR_NEWS_SOURCE_TIMEOUT", "45"))


def _retry_call(fn, attempts: int | None = None):
    attempts = attempts or max(1, int(os.environ.get("VR_COLLECT_RETRY_ATTEMPTS", "3")))
    base = max(0.1, float(os.environ.get("VR_COLLECT_RETRY_BASE", "0.8")))
    last_error = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(base * (2 ** attempt))
    raise last_error


def _call_with_timeout(fn, timeout: float = NEWS_SOURCE_TIMEOUT):
    result: queue.Queue = queue.Queue(maxsize=1)

    def run():
        try:
            result.put((True, fn()))
        except Exception as exc:
            result.put((False, exc))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"数据源抓取超过 {timeout:g} 秒")
    ok, value = result.get_nowait()
    if not ok:
        raise value
    return value


def _news_ts(value: object, fallback: int) -> int:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%m-%d %H:%M", "%m-%d %H:%M:%S", "%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            today = datetime.now(CN)
            if fmt.startswith("%m"):
                parsed = parsed.replace(year=today.year)
            elif fmt.startswith("%H"):
                parsed = parsed.replace(year=today.year, month=today.month, day=today.day)
            return int(parsed.replace(tzinfo=CN).timestamp())
        except ValueError:
            continue
    return fallback


def _ak():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("市场采集需要 akshare") from exc
    return ak


def _safe(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return str(value)
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _records(df) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    return [{str(k): _safe(v) for k, v in row.items()} for _, row in df.iterrows()]


def _trade_date(now: datetime | None = None, after_close: bool = False) -> str:
    now = now or datetime.now(CN)
    d = now.date()
    if after_close and now.hour * 60 + now.minute < 15 * 60 + 5:
        d -= timedelta(days=1)
    while True:
        opened = None
        if store.enabled():
            try:
                opened = store.is_trading_day(d)
            except store.StoreUnavailable:
                pass
        if opened is True or (opened is None and d.weekday() < 5):
            break
        d -= timedelta(days=1)
    return str(d)


def _run(job: str, source: str, fn):
    log_id = store.start_log(job, source)
    try:
        result = fn()
        count = result.get("count", 0) if isinstance(result, dict) else 0
        errors = result.get("errors") if isinstance(result, dict) else None
        if errors:
            detail = json.dumps({"count": count, "errors": errors}, ensure_ascii=False)
            store.finish_log(log_id, "partial", detail)
        else:
            store.finish_log(log_id, "ok", f"写入 {count} 条")
        return result
    except Exception as exc:
        store.finish_log(log_id, "fail", str(exc))
        raise


def collect_news() -> dict:
    def work():
        ak = _ak()
        sources = {
            "em": lambda: ak.stock_info_global_em(),
            "ths": lambda: ak.stock_info_global_ths(),
            "cls": lambda: ak.stock_info_global_cls(symbol="全部"),
        }
        total, errors, added_by_source = 0, {}, {}
        today, now = _trade_date(), int(time.time())
        for source, fetch in sources.items():
            try:
                rows = _records(_retry_call(lambda: _call_with_timeout(fetch)))
                normalized = []
                for row in rows:
                    content = str(row.get("内容") or "").strip()
                    title = str(row.get("标题") or "").strip() or content[:60]
                    if not title:
                        continue
                    pub_time = str(row.get("发布时间") or "")
                    normalized.append({
                        "source": source,
                        "title": title[:500],
                        "summary": str(row.get("摘要") or content)[:1000],
                        "link": str(row.get("链接") or ""),
                        "pub_time": pub_time,
                        "pub_ts": _news_ts(pub_time, now),
                        "content_hash": hashlib.md5((source + "|" + title[:200]).encode()).hexdigest(),
                        "created_at": now,
                    })
                added = store.upsert_news(normalized)
                store.save_snapshot("news_feed", source, "", today, now, normalized)
                total += len(normalized)
                added_by_source[source] = added
            except Exception as exc:
                errors[source] = str(exc)
        if total == 0:
            raise RuntimeError("三个财经资讯源均无可用数据：" + json.dumps(errors, ensure_ascii=False))
        return {"count": total, "added": added_by_source, "errors": errors}
    return _run("news", "em,ths,cls", work)


def _eastmoney_hot_rank() -> list[dict]:
    """Fetch Eastmoney ranks directly and use Tencent for quote enrichment.

    Eastmoney's ranking endpoint is independent from ``push2``.  The latter is
    unavailable on some local networks, so a quote failure must not discard the
    otherwise complete top-100 ranking snapshot.
    """
    import requests
    import astock

    session = requests.Session()
    session.trust_env = False
    response = session.post(
        "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
        json={"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38",
              "marketType": "", "pageNo": 1, "pageSize": 100},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
    )
    response.raise_for_status()
    ranks = response.json().get("data") or []
    if not ranks:
        raise RuntimeError("东方财富热榜排名为空")
    codes = [str(item.get("sc") or "")[2:] for item in ranks]
    try:
        quotes = astock.tencent_quote([code for code in codes if code])
    except Exception:
        quotes = {}
    rows = []
    for item, code in zip(ranks, codes):
        quote = quotes.get(code) or {}
        price = quote.get("price")
        change_pct = quote.get("change_pct")
        rows.append({
            "当前排名": item.get("rk"), "代码": item.get("sc") or code,
            "股票名称": quote.get("name"), "最新价": price,
            "涨跌额": price * change_pct / 100 if price is not None and change_pct is not None else None,
            "涨跌幅": change_pct,
        })
    return rows


def collect_hot() -> dict:
    def work():
        ak = _ak()
        now = datetime.now(CN)
        batch = int(now.timestamp()) // 1800 * 1800
        td, total, errors = str(now.date()), 0, {}
        def ths_hot():
            import requests
            response = requests.get(
                "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
                params={"stock_type": "a", "type": "hour", "list_type": "normal"},
                headers={"Accept": "application/json, text/plain, */*",
                         "Origin": "https://eq.10jqka.com.cn", "Referer": "https://eq.10jqka.com.cn/",
                         "User-Agent": "Mozilla/5.0"}, timeout=15,
            )
            response.raise_for_status()
            return (response.json().get("data") or {}).get("stock_list") or []

        calls = {
            "em": _eastmoney_hot_rank,
            "ths": ths_hot,
            "baidu": lambda: ak.stock_hot_search_baidu(symbol="A股", date=now.strftime("%Y%m%d"), time="今日"),
        }
        for source, fetch in calls.items():
            try:
                value = _retry_call(fetch)
                rows = value if isinstance(value, list) else _records(value)
                store.save_snapshot("hot", source, "", td, batch, rows)
                total += len(rows)
            except Exception as exc:
                errors[source] = str(exc)
        if total == 0:
            raise RuntimeError("热榜数据为空：" + json.dumps(errors, ensure_ascii=False))
        return {"count": total, "errors": errors, "batch_ts": batch}
    return _run("hot", "em,ths,baidu", work)


def collect_fund_flow(realtime: bool = True) -> dict:
    job = "fund_flow" if realtime else "fund_flow_daily"
    def normalize(rows: list[dict], period: str) -> list[dict]:
        normalized = []
        for index, row in enumerate(rows):
            if realtime:
                normalized.append({
                    "rank": row.get("rank") or row.get("序号") or index + 1,
                    "board": row.get("board") or row.get("行业") or "",
                    "net": row.get("net") if row.get("net") is not None else row.get("净额"),
                    "inflow": row.get("inflow") if row.get("inflow") is not None else row.get("流入资金"),
                    "outflow": row.get("outflow") if row.get("outflow") is not None else row.get("流出资金"),
                    "change_pct": row.get("change_pct") if row.get("change_pct") is not None else row.get("行业-涨跌幅"),
                    "board_index": row.get("board_index") if row.get("board_index") is not None else row.get("行业指数"),
                    "company_count": row.get("company_count") if row.get("company_count") is not None else row.get("公司家数"),
                    "leader": row.get("leader") or row.get("领涨股") or "",
                    "leader_pct": row.get("leader_pct") if row.get("leader_pct") is not None else row.get("领涨股-涨跌幅"),
                    "price": row.get("price") if row.get("price") is not None else row.get("当前价"),
                })
            else:
                stage_pct = row.get("stage_pct") if row.get("stage_pct") is not None else row.get("阶段涨跌幅")
                normalized.append({
                    "rank": row.get("rank") or row.get("序号") or index + 1,
                    "board": row.get("board") or row.get("行业") or "",
                    "company_count": row.get("company_count") if row.get("company_count") is not None else row.get("公司家数"),
                    "board_index": row.get("board_index") if row.get("board_index") is not None else row.get("行业指数"),
                    "stage_pct": str(stage_pct).replace("%", "") if stage_pct is not None else None,
                    "inflow": row.get("inflow") if row.get("inflow") is not None else row.get("流入资金"),
                    "outflow": row.get("outflow") if row.get("outflow") is not None else row.get("流出资金"),
                    "net": row.get("net") if row.get("net") is not None else row.get("净额"),
                    "period": period,
                })
        return [row for row in normalized if row["board"]]

    def work():
        ak = _ak()
        td, now, total, errors = _trade_date(after_close=not realtime), int(time.time()), 0, {}
        periods = {"live": "即时"} if realtime else {p: f"{p}日排行" for p in ("3", "5", "10", "20")}
        for scope, fetch in (("concept", ak.stock_fund_flow_concept), ("industry", ak.stock_fund_flow_industry)):
            for period, symbol in periods.items():
                try:
                    rows = normalize(_records(_retry_call(lambda: fetch(symbol=symbol))), period)
                    batch = now if realtime else 0
                    store.save_snapshot("fund_flow" if realtime else "fund_flow_daily", "ths",
                                        f"{scope}:{period}", td, batch, rows)
                    total += len(rows)
                except Exception as exc:
                    errors[f"{scope}:{period}"] = str(exc)
        if total == 0:
            raise RuntimeError("资金流数据为空：" + json.dumps(errors, ensure_ascii=False))
        result = {"count": total, "errors": errors}
        if not realtime:
            retention = int(os.environ.get("VR_FUND_FLOW_RETENTION_DAYS", "90"))
            result["pruned"] = store.prune("fund_flow", datetime.now(CN).date() - timedelta(days=retention))
        return result
    return _run(job, "ths", work)


def collect_stocks() -> dict:
    def work():
        td = _trade_date(after_close=True)
        rows = _records(_retry_call(lambda: _ak().stock_info_a_code_name()))
        store.save_snapshot("stocks", "akshare", "", td, 0, rows)
        synced = store.sync_stock_universe(rows, td)
        return {"count": len(rows), "synced": synced, "trade_date": td}
    return _run("stocks", "akshare", work)


def collect_trade_calendar() -> dict:
    def work():
        rows = _records(_retry_call(lambda: _ak().tool_trade_date_hist_sina()))
        dates = [str(row.get("trade_date") or row.get("日期") or "")[:10] for row in rows]
        dates = [value for value in dates if value]
        if not dates:
            raise RuntimeError("交易日历为空")
        start = date.fromisoformat(min(dates))
        end = date.fromisoformat(max(dates))
        count = store.sync_trade_calendar(dates, start, end)
        return {"count": count, "open_days": len(dates), "start": str(start), "end": str(end)}
    return _run("trade_calendar", "sina", work)


def collect_boards() -> dict:
    def work():
        ak = _ak()
        td, total, errors = _trade_date(after_close=True), 0, {}
        calls = {
            "em": lambda: ak.stock_board_concept_name_em(),
            "ths": lambda: ak.stock_board_concept_name_ths(),
            "ind": lambda: ak.stock_board_industry_name_em(),
        }
        for source, fetch in calls.items():
            try:
                raw_rows = _records(_retry_call(fetch))
                rows = [{
                    **row,
                    "board": row.get("board") or row.get("板块名称") or row.get("概念名称") or row.get("行业名称") or row.get("名称") or "",
                    "price": row.get("price") if row.get("price") is not None else row.get("最新价"),
                    "change_pct": row.get("change_pct") if row.get("change_pct") is not None else row.get("涨跌幅"),
                    "total_mv": row.get("total_mv") if row.get("total_mv") is not None else row.get("总市值"),
                    "turnover": row.get("turnover") if row.get("turnover") is not None else row.get("换手率"),
                    "up_count": row.get("up_count") if row.get("up_count") is not None else row.get("上涨家数"),
                    "down_count": row.get("down_count") if row.get("down_count") is not None else row.get("下跌家数"),
                    "leader": row.get("leader") or row.get("领涨股票") or row.get("领涨股") or "",
                } for row in raw_rows]
                rows = [row for row in rows if row["board"]]
                store.save_snapshot("boards", source, "daily", td, 0, rows)
                total += len(rows)
            except Exception as exc:
                errors[source] = str(exc)
        if total == 0:
            raise RuntimeError("板块数据为空：" + json.dumps(errors, ensure_ascii=False))
        return {"count": total, "errors": errors}
    return _run("boards", "em,ths,ind", work)


def collect_boards_realtime() -> dict:
    """Collect Eastmoney concept-board quotes during trading hours."""
    def work():
        import requests
        response = requests.get(
            "https://push2delay.eastmoney.com/api/qt/clist/get",
            params={"pn": "1", "pz": "500", "po": "1", "np": "1",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": "2", "invt": "2",
                    "fid": "f12", "fs": "m:90 t:3 f:!50",
                    "fields": "f2,f3,f4,f8,f12,f14,f20,f107,f104,f33,f128"},
            timeout=20,
        )
        response.raise_for_status()
        items = (response.json().get("data") or {}).get("diff") or []
        if isinstance(items, dict):
            items = list(items.values())
        rows = [{
            "board": str(item.get("f14") or ""), "board_code": str(item.get("f12") or ""),
            "price": _safe(item.get("f2")), "change_pct": _safe(item.get("f3")),
            "change_amt": _safe(item.get("f4")), "turnover": _safe(item.get("f8")),
            "total_mv": _safe(item.get("f20")), "up_count": _safe(item.get("f107")),
            "down_count": _safe(item.get("f104")), "leader": str(item.get("f33") or ""),
            "leader_pct": _safe(item.get("f128")),
        } for item in items if item.get("f14")]
        if not rows:
            raise RuntimeError("东方财富概念板块实时数据为空")
        now = int(time.time())
        store.save_snapshot("boards", "em", "live", _trade_date(), now, rows)
        return {"count": len(rows), "batch_ts": now}
    return _run("boards_realtime", "em", work)


POOL_CALLS = {
    "zt": "stock_zt_pool_em",
    "strong": "stock_zt_pool_strong_em",
    "zbgc": "stock_zt_pool_zbgc_em",
    "dtgc": "stock_zt_pool_dtgc_em",
}


def collect_market_pools(trade_date: str | None = None) -> dict:
    def work():
        ak, td, total, errors = _ak(), trade_date or _trade_date(after_close=True), 0, {}
        ymd = td.replace("-", "")
        for scope, name in POOL_CALLS.items():
            try:
                rows = _records(_retry_call(lambda: getattr(ak, name)(date=ymd)))
                store.save_snapshot("market_pool", "eastmoney", scope, td, 0, rows)
                total += len(rows)
            except Exception as exc:
                errors[scope] = str(exc)
        if total == 0:
            raise RuntimeError("行情池数据为空：" + json.dumps(errors, ensure_ascii=False))
        return {"count": total, "errors": errors, "trade_date": td}
    return _run("market_pool", "eastmoney", work)


TECH_RANKS = {
    "cxg": ("stock_rank_cxg_ths", ["创月新高", "半年新高", "一年新高", "历史新高"]),
    "cxd": ("stock_rank_cxd_ths", ["创月新低", "半年新低", "一年新低", "历史新低"]),
    "xstp": ("stock_rank_xstp_ths", [f"{n}日均线" for n in (5, 10, 20, 30, 60, 90, 250, 500)]),
    "xxtp": ("stock_rank_xxtp_ths", [f"{n}日均线" for n in (5, 10, 20, 30, 60, 90, 250, 500)]),
    "cxfl": ("stock_rank_cxfl_ths", [""]), "cxsl": ("stock_rank_cxsl_ths", [""]),
    "ljqd": ("stock_rank_ljqd_ths", [""]), "ljqs": ("stock_rank_ljqs_ths", [""]),
    "lxsz": ("stock_rank_lxsz_ths", [""]), "lxxd": ("stock_rank_lxxd_ths", [""]),
    "xzjp": ("stock_rank_xzjp_ths", [""]),
}


def collect_tech_rank(indicators: list[str] | None = None) -> dict:
    def work():
        ak, td, total, errors = _ak(), _trade_date(after_close=True), 0, {}
        for key, (name, params) in TECH_RANKS.items():
            if indicators and key not in indicators:
                continue
            for param in params:
                try:
                    fn = getattr(ak, name)
                    rows = _records(_retry_call(lambda: fn(symbol=param) if param else fn()))
                    store.save_snapshot("tech_rank", "ths", f"{key}:{param}", td, 0, rows)
                    total += len(rows)
                except Exception as exc:
                    errors[f"{key}:{param}"] = str(exc)
                time.sleep(1.0)
        if total == 0:
            raise RuntimeError("技术指标数据为空：" + json.dumps(errors, ensure_ascii=False))
        return {"count": total, "errors": errors, "trade_date": td}
    return _run("tech_rank", "ths", work)


def collect_lhb() -> dict:
    def work():
        td = _trade_date(after_close=True)
        rows = _records(_retry_call(lambda: _ak().stock_lhb_detail_em(
            start_date=td.replace("-", ""), end_date=td.replace("-", ""))))
        store.save_snapshot("lhb", "eastmoney", "", td, 0, rows)
        return {"count": len(rows), "trade_date": td}
    return _run("lhb", "eastmoney", work)


def collect_lhb_details() -> dict:
    """Backfill per-stock LHB seat details for the latest saved daily list."""
    def work():
        import astock
        snapshot = store.latest_snapshot("lhb", "eastmoney", "")
        if not snapshot:
            raise RuntimeError("尚无龙虎榜日榜，请先采集龙虎榜")
        td = str(snapshot["trade_date"])
        rows = snapshot.get("payload") or []
        targets: list[tuple[str, str]] = []
        for row in rows if isinstance(rows, list) else []:
            data = row
            if isinstance(row.get("data"), str):
                try:
                    data = {**row, **json.loads(row["data"])}
                except (TypeError, json.JSONDecodeError):
                    pass
            code = str(data.get("代码") or data.get("code") or "")
            reason = str(data.get("上榜原因") or data.get("reason") or "")
            target = (code, reason)
            if code and target not in targets:
                targets.append(target)
        total, errors = 0, {}
        for code, reason in targets:
            try:
                payload = astock.dragon_tiger_board(code, trade_date=td, look_back=0, reason=reason)
                scope = lhb_detail_scope(code, reason)
                store.save_snapshot("lhb_detail", "eastmoney-direct", scope, td, 0, payload,
                                    {"code": code, "reason": reason})
                total += 1
            except Exception as exc:
                errors[f"{code}:{reason}"] = str(exc)
        if total == 0:
            raise RuntimeError("龙虎榜详情补抓失败：" + json.dumps(errors, ensure_ascii=False))
        return {"count": total, "errors": errors, "trade_date": td}
    return _run("lhb_detail", "eastmoney", work)


def lhb_detail_scope(code: str, reason: str = "") -> str:
    if not reason:
        return code
    digest = hashlib.md5(reason.encode("utf-8")).hexdigest()[:16]
    return f"{code}:{digest}"


def migrate_legacy_lhb_details() -> dict:
    """Convert imported legacy seat rows into reason-scoped detail snapshots."""
    def work():
        converted = skipped = 0
        with store.connection() as conn:
            rows = conn.execute(
                """SELECT trade_date,scope AS code,payload FROM market_snapshots
                   WHERE dataset='lhb_detail' AND source='eastmoney'
                   ORDER BY trade_date,scope"""
            ).fetchall()
            for snapshot in rows:
                grouped: dict[str, list[dict]] = {}
                for item in snapshot["payload"] if isinstance(snapshot["payload"], list) else []:
                    reason = str(item.get("reason") or "")
                    if reason:
                        grouped.setdefault(reason, []).append(item)
                for reason, items in grouped.items():
                    scope = lhb_detail_scope(snapshot["code"], reason)
                    exists = conn.execute(
                        """SELECT 1 FROM market_snapshots WHERE dataset='lhb_detail'
                           AND source='eastmoney-direct' AND scope=%s AND trade_date=%s""",
                        (scope, snapshot["trade_date"]),
                    ).fetchone()
                    if exists:
                        skipped += 1
                        continue
                    seats = {"buy": [], "sell": []}
                    institution = {"buy_amt": 0.0, "sell_amt": 0.0, "net_amt": 0.0}
                    for item in items:
                        try:
                            raw = json.loads(item.get("data") or "{}")
                        except (TypeError, json.JSONDecodeError):
                            raw = {}
                        side = "buy" if item.get("flag") == "买入" else "sell"
                        seat = {
                            "name": item.get("seat") or raw.get("交易营业部名称") or "",
                            "buy_amt": round(float(raw.get("买入金额") or 0) / 10000, 1),
                            "sell_amt": round(float(raw.get("卖出金额") or 0) / 10000, 1),
                            "net": round(float(raw.get("净额") or 0) / 10000, 1),
                        }
                        seats[side].append(seat)
                        if seat["name"] == "机构专用":
                            institution[f"{side}_amt"] += seat["buy_amt" if side == "buy" else "sell_amt"]
                    seats["buy"] = seats["buy"][:5]
                    seats["sell"] = seats["sell"][:5]
                    institution = {key: round(value, 1) for key, value in institution.items()}
                    institution["net_amt"] = round(institution["buy_amt"] - institution["sell_amt"], 1)
                    payload = {"records": [{"date": str(snapshot["trade_date"]), "reason": reason,
                                              "net_buy": 0.0, "turnover": 0.0}],
                               "seats": seats, "institution": institution}
                    conn.execute(
                        """INSERT INTO market_snapshots(dataset,source,scope,trade_date,batch_ts,payload,metadata)
                           VALUES ('lhb_detail','eastmoney-direct',%s,%s,0,%s::jsonb,%s::jsonb)
                           ON CONFLICT(dataset,source,scope,trade_date,batch_ts) DO NOTHING""",
                        (scope, snapshot["trade_date"], json.dumps(payload, ensure_ascii=False),
                         json.dumps({"code": snapshot["code"], "reason": reason,
                                     "migrated_from": "eastmoney"}, ensure_ascii=False)),
                    )
                    converted += 1
        return {"count": converted, "skipped": skipped}
    return _run("lhb_history_migration", "postgresql", work)


def collect_stock_archive() -> dict:
    import stock_archive
    limit = max(0, int(os.environ.get("VR_STOCK_COLLECT_LIMIT", "0")))
    return _run("stock_archive", "multi-source", lambda: stock_archive.collect_all(limit=limit))


def retry_stock_archive() -> dict:
    import stock_archive
    limit = max(1, int(os.environ.get("VR_STOCK_RETRY_LIMIT", "500")))
    return _run("stock_archive_retry", "multi-source", lambda: stock_archive.retry_failed(limit=limit))


def collect_daily_review() -> dict:
    def work():
        import daily_review
        payload = daily_review.capture()
        return {"count": 1, "trade_date": payload["date"]}
    return _run("daily_review", "vibe", work)


JOBS = {
    "trade_calendar": collect_trade_calendar,
    "news": collect_news,
    "hot": collect_hot,
    "fund_flow": collect_fund_flow,
    "fund_flow_daily": lambda: collect_fund_flow(False),
    "stocks": collect_stocks,
    "stock_archive": collect_stock_archive,
    "stock_archive_retry": retry_stock_archive,
    "boards": collect_boards,
    "boards_realtime": collect_boards_realtime,
    "market_pool": collect_market_pools,
    "tech_rank": collect_tech_rank,
    "lhb": collect_lhb,
    "lhb_detail": collect_lhb_details,
    "lhb_history_migration": migrate_legacy_lhb_details,
    "daily_review": collect_daily_review,
}


def run_job(name: str):
    if name not in JOBS:
        raise ValueError(f"未知采集任务：{name}")
    with store.advisory_lock(name) as locked:
        if not locked:
            log_id = store.start_log(name, "postgresql-lock")
            store.finish_log(log_id, "skipped", "另一个进程正在执行同名任务")
            return {"count": 0, "skipped": True}
        return JOBS[name]()
