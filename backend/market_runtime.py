"""Single-process scheduler for PostgreSQL-backed market collectors."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import market_collectors
import market_store


CN = ZoneInfo("Asia/Shanghai")
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vr-market")
_RUNNING: set[str] = set()
_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LAST_TRIGGER: dict[str, float] = {}
_DONE: dict[str, str] = {}

NEWS_INTERVAL = int(os.environ.get("VR_NEWS_INTERVAL", "60"))
HOT_INTERVAL = int(os.environ.get("VR_HOT_INTERVAL", "1800"))
REALTIME_INTERVAL = int(os.environ.get("VR_REALTIME_INTERVAL", "60"))


def submit(name: str) -> bool:
    if name not in market_collectors.JOBS:
        raise ValueError(f"未知采集任务：{name}")
    with _LOCK:
        if name in _RUNNING:
            return False
        _RUNNING.add(name)

    def run():
        try:
            market_collectors.run_job(name)
        finally:
            with _LOCK:
                _RUNNING.discard(name)
    _POOL.submit(run)
    return True


def running() -> list[str]:
    with _LOCK:
        return sorted(_RUNNING)


def status() -> dict:
    return {
        "running": running(),
        "intervals": {"news": NEWS_INTERVAL, "hot": HOT_INTERVAL, "fund_flow": REALTIME_INTERVAL,
                      "boards_realtime": REALTIME_INTERVAL},
        "last_trigger": dict(_LAST_TRIGGER),
        "daily": {"startup": ["trade_calendar"],
                  "15:05": ["fund_flow_daily", "boards", "stocks", "market_pool", "tech_rank", "daily_review"],
                  "16:00": "retry_missing", "18:30": ["lhb"], "18:45": ["lhb_detail"],
                  "19:00": ["stock_archive"], "21:00": ["stock_archive_retry"]},
    }


def _open_day(now: datetime) -> bool:
    if market_store.enabled():
        try:
            opened = market_store.is_trading_day(now.date())
            if opened is not None:
                return opened
        except market_store.StoreUnavailable:
            pass
    return now.weekday() < 5


def _trading(now: datetime) -> bool:
    if not _open_day(now):
        return False
    hm = now.hour * 60 + now.minute
    return 570 <= hm <= 690 or 780 <= hm <= 900


def _interval_due(stamp: float, previous: float, interval: int) -> bool:
    """Align periodic jobs to wall-clock minute/5-minute/half-hour buckets."""
    return int(stamp // interval) > int(previous // interval)


def _loop():
    while not _STOP.wait(15):
        now, stamp = datetime.now(CN), time.time()
        day = str(now.date())
        if _DONE.get("calendar") != day:
            _DONE["calendar"] = day
            submit("trade_calendar")
        schedule: list[tuple[str, int]] = [("news", NEWS_INTERVAL), ("hot", HOT_INTERVAL)]
        if _trading(now):
            schedule += [("fund_flow", REALTIME_INTERVAL), ("boards_realtime", REALTIME_INTERVAL)]
        for job, interval in schedule:
            if _interval_due(stamp, _LAST_TRIGGER.get(job, 0), interval):
                if submit(job):
                    _LAST_TRIGGER[job] = stamp
        hm = now.hour * 60 + now.minute
        open_day = _open_day(now)
        if open_day and hm >= 905 and _DONE.get("daily") != day:
            _DONE["daily"] = day
            for job in ("fund_flow_daily", "boards", "stocks", "market_pool", "tech_rank", "daily_review"):
                submit(job)
        if open_day and hm >= 960 and _DONE.get("retry") != day:
            _DONE["retry"] = day
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            for job in ("hot", "fund_flow", "boards_realtime", "tech_rank", "daily_review"):
                try:
                    succeeded = market_store.job_succeeded_since(job, midnight)
                except market_store.StoreUnavailable:
                    succeeded = False
                if not succeeded:
                    submit(job)
        if open_day and hm >= 1110 and _DONE.get("lhb") != day:
            _DONE["lhb"] = day
            submit("lhb")
        if open_day and hm >= 1125 and _DONE.get("lhb_detail") != day:
            _DONE["lhb_detail"] = day
            submit("lhb_detail")
        if open_day and hm >= 1140 and _DONE.get("stock_archive") != day:
            _DONE["stock_archive"] = day
            submit("stock_archive")
        if (open_day and hm >= 1260 and _DONE.get("stock_archive_retry") != day
                and "stock_archive" not in running()):
            _DONE["stock_archive_retry"] = day
            submit("stock_archive_retry")


def start() -> None:
    global _THREAD
    if not market_store.enabled() or os.environ.get("VR_COLLECTOR_ENABLED", "1") != "1":
        return
    market_store.init_db()
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="vr-market-scheduler", daemon=True)
    _THREAD.start()


def stop() -> None:
    _STOP.set()
