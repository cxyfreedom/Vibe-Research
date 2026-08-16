"""PostgreSQL-backed daily archive for every dataset shown on the A-share page."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

import astock
import market_store as store


CN = ZoneInfo("Asia/Shanghai")


def _reports(code: str):
    rows = astock.eastmoney_reports(code, max_pages=2)
    for row in rows:
        row["pdfUrl"] = astock.pdf_url(row.get("infoCode", "")) if row.get("infoCode") else None
    return rows


FETCHERS: dict[str, Callable[[str], object]] = {
    "valuation": astock.full_valuation,
    "reports": _reports,
    "percentile": astock.valuation_percentile,
    "financials": astock.financials,
    "announcements": astock.announcements,
    "news": astock.stock_news,
    "margin": astock.margin_trading,
    "block_trade": astock.block_trade,
    "holders": astock.holder_num_change,
    "dividend": astock.dividend_history,
    "fund_flow": astock.stock_fund_flow_120d,
    "dragon_tiger": astock.dragon_tiger_board,
    "lockup": astock.lockup_expiry,
    "blocks": astock.concept_blocks,
    "hot_concepts": astock.hot_concepts,
    "investor_qa": astock.investor_qa,
}
DATA_TYPES = ("quote", *FETCHERS.keys())


def _fetch_with_retry(data_type: str, code: str) -> object:
    attempts = max(1, int(os.environ.get("VR_STOCK_COLLECT_ATTEMPTS", "3")))
    base = max(0.1, float(os.environ.get("VR_STOCK_RETRY_BASE", "0.8")))
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return FETCHERS[data_type](code)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(base * (2 ** attempt))
    assert last_error is not None
    raise last_error


def archive_date(day: date | None = None) -> str:
    day = day or datetime.now(CN).date()
    if store.enabled():
        opened = store.is_trading_day(day)
        if opened:
            return str(day)
        previous = store.previous_trading_day(day)
        if previous:
            return previous
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return str(day)


def read_through(code: str, data_type: str, fetch: Callable[[], object],
                 trade_date: str | None = None) -> object:
    """Read the current trading-day snapshot first, then fetch and persist on miss."""
    target = trade_date or archive_date()
    if store.enabled():
        hit = store.latest_stock_data(code, data_type, target)
        if hit:
            return hit["payload"]
    payload = fetch()
    if store.enabled():
        store.save_stock_bundle(code, target, {data_type: payload})
    return payload


def _collect_quotes(codes: list[str], trade_date: str, force: bool,
                    done_states: set[tuple[str, str]]) -> tuple[int, int]:
    completed = failed = 0
    pending = [code for code in codes if force or (code, "quote") not in done_states]
    for offset in range(0, len(pending), 80):
        chunk = pending[offset:offset + 80]
        try:
            quotes = astock.tencent_quote(chunk)
            found = {code: quotes[code] for code in chunk if quotes.get(code)}
            missing = [code for code in chunk if code not in found]
            store.save_stock_quotes(trade_date, found, missing)
            completed += len(found)
            failed += len(missing)
        except Exception as exc:
            for code in chunk:
                store.save_stock_bundle(code, trade_date, {}, {"quote": str(exc)})
                failed += 1
        time.sleep(0.1)
    completed += len(codes) - len(pending)
    return completed, failed


def _collect_fund_flows(codes: list[str], trade_date: str, force: bool,
                        done_states: set[tuple[str, str]]) -> tuple[int, int]:
    pending = [code for code in codes if force or (code, "fund_flow") not in done_states]
    if not pending:
        return len(codes), 0
    snapshots = astock.all_stock_fund_flow_snapshot(trade_date)
    found = {code: snapshots[code] for code in pending if code in snapshots}
    missing = [code for code in pending if code not in found]
    store.save_stock_dataset_batch(
        trade_date, "fund_flow", found, missing, source="ths",
        missing_error="同花顺全市场资金流未覆盖该股票",
    )
    return len(codes) - len(pending) + len(found), len(missing)


def collect_all(trade_date: str | None = None, limit: int = 0,
                data_types: list[str] | None = None, force: bool = False,
                codes: list[str] | None = None) -> dict:
    """Collect all stock-page datasets with per-code/type checkpoints."""
    target = trade_date or archive_date()
    stocks = store.stock_codes(limit=limit)
    if codes:
        wanted = set(codes)
        stocks = [row for row in store.stock_codes() if row["code"] in wanted]
    if not stocks:
        raise RuntimeError("股票列表为空，请先运行 stocks 任务")
    selected = list(data_types or DATA_TYPES)
    unknown = sorted(set(selected) - set(DATA_TYPES))
    if unknown:
        raise ValueError("未知个股数据类型：" + ",".join(unknown))
    total_tasks = len(stocks) * len(selected)
    run_id = store.start_stock_run(target, len(stocks), total_tasks)
    start_stamp = time.time()
    completed = failed = 0
    errors: dict[str, str] = {}
    done_states = set() if force else store.stock_data_done_set(target, selected)
    try:
        if "quote" in selected:
            done, bad = _collect_quotes([row["code"] for row in stocks], target, force, done_states)
            completed += done
            failed += bad
        if "fund_flow" in selected:
            done, bad = _collect_fund_flows([row["code"] for row in stocks], target, force, done_states)
            completed += done
            failed += bad
        detail_types = [item for item in selected if item not in {"quote", "fund_flow"}]
        store.update_stock_run(run_id, completed, failed, detail={"phase": "details", "stocks_done": 0})
        if not detail_types:
            status = "ok" if failed == 0 else "partial"
            result = {"run_id": run_id, "trade_date": target, "stocks": len(stocks),
                      "completed": completed, "failed": failed, "error_samples": {}}
            store.update_stock_run(run_id, completed, failed, status, result)
            return {"count": completed, **result}
        delay = max(0.0, float(os.environ.get("VR_STOCK_COLLECT_DELAY", "0.2")))
        workers = max(1, min(8, int(os.environ.get("VR_STOCK_COLLECT_WORKERS", "4"))))

        def collect_stock(stock: dict) -> tuple[str, int, int, dict[str, str]]:
            code = stock["code"]
            payloads, current_errors = {}, {}
            current_completed = current_failed = 0
            for data_type in detail_types:
                if not force and (code, data_type) in done_states:
                    current_completed += 1
                    continue
                try:
                    payload = _fetch_with_retry(data_type, code)
                    if data_type == "valuation" and not payload:
                        raise RuntimeError("估值数据为空")
                    payloads[data_type] = payload
                    current_completed += 1
                except Exception as exc:
                    current_errors[data_type] = str(exc)
                    current_failed += 1
                if delay:
                    time.sleep(delay)
            store.save_stock_bundle(code, target, payloads, current_errors)
            return code, current_completed, current_failed, current_errors

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stock-archive") as pool:
            futures = {pool.submit(collect_stock, stock): stock["code"] for stock in stocks}
            for index, future in enumerate(as_completed(futures), 1):
                code, current_completed, current_failed, current_errors = future.result()
                completed += current_completed
                failed += current_failed
                errors.update({f"{code}:{kind}": error for kind, error in current_errors.items()})
                if index % 10 == 0 or index == len(stocks):
                    elapsed = max(time.time() - start_stamp, 0.001)
                    rate = index / elapsed
                    store.update_stock_run(run_id, completed, failed, detail={
                        "current": code, "stocks_done": index, "errors": len(errors),
                        "workers": workers, "stocks_per_minute": round(rate * 60, 2),
                        "eta_seconds": round((len(stocks) - index) / max(rate, 0.001)),
                    })
        status = "ok" if failed == 0 else "partial"
        result = {"run_id": run_id, "trade_date": target, "stocks": len(stocks),
                  "completed": completed, "failed": failed,
                  "error_samples": dict(list(errors.items())[:20])}
        store.update_stock_run(run_id, completed, failed, status, result)
        return {"count": completed, **result}
    except Exception as exc:
        store.update_stock_run(run_id, completed, failed, "fail", {"error": str(exc)})
        raise


def retry_failed(trade_date: str | None = None, limit: int = 500) -> dict:
    target = trade_date or archive_date()
    failures = store.failed_stock_tasks(target, limit)
    if not failures:
        return {"count": 0, "trade_date": target, "message": "没有失败项"}
    codes = sorted({row["code"] for row in failures})
    data_types = sorted({row["data_type"] for row in failures})
    return collect_all(target, data_types=data_types, force=True, codes=codes)
