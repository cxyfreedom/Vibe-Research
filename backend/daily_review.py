"""Persisted daily-review snapshots."""

from __future__ import annotations

import threading
import time
from datetime import date as date_type, datetime
from zoneinfo import ZoneInfo

import astock
import market
import market_store


CN = ZoneInfo("Asia/Shanghai")

_CALENDAR_LOCK = threading.Lock()
_CALENDAR_CACHE: tuple[float, frozenset[date_type]] = (0, frozenset())
_CAPTURE_LOCK = threading.Lock()
_CAPTURE_CACHE: tuple[float, dict | None] = (0, None)


def _date(value) -> str:
    return str(value or "")[:10]


def _canonical_activity(activity: dict) -> dict:
    """Keep source fields and add the stable keys used by persisted reviews."""
    aliases = {
        "up_count": "上涨", "down_count": "下跌", "flat_count": "平盘",
        "paused_count": "停牌", "limit_up": "涨停", "limit_down": "跌停",
        "real_limit_up": "真实涨停", "real_limit_down": "真实跌停",
        "activity": "活跃度",
    }
    result = dict(activity)
    result.pop("median_rise", None)
    for target, source in aliases.items():
        if target not in result and source in activity:
            value = activity[source]
            if target == "activity" and isinstance(value, str):
                value = value.rstrip("%")
            result[target] = value
    return result


def is_trade_date(day: date_type | None = None) -> bool:
    """Check the Shanghai exchange calendar; weekends never need a network call."""
    day = day or datetime.now(CN).date()
    if day.weekday() >= 5:
        return False
    global _CALENDAR_CACHE
    now = time.time()
    with _CALENDAR_LOCK:
        stamp, dates = _CALENDAR_CACHE
        if not dates or now - stamp >= 12 * 3600:
            try:
                import requests
                from akshare.tool.trade_date_hist import hk_js_decode, py_mini_racer

                session = requests.Session()
                session.trust_env = False
                response = session.get(
                    "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt",
                    headers={"User-Agent": astock.UA},
                    timeout=15,
                )
                response.raise_for_status()
                encoded = response.text.split("=", 1)[1].split(";", 1)[0].replace('"', "")
                runtime = py_mini_racer.MiniRacer()
                runtime.eval(hk_js_decode)
                values = runtime.call("d", encoded)
                dates = frozenset(date_type.fromisoformat(str(value)[:10]) for value in values)
                _CALENDAR_CACHE = (now, dates)
            except Exception:
                return day.weekday() < 5
    return day in dates


def capture_cached(ttl: int = 60) -> dict:
    """Coalesce duplicate live requests and keep a short intraday cache."""
    global _CAPTURE_CACHE
    now = time.time()
    stamp, payload = _CAPTURE_CACHE
    if payload is not None and now - stamp < ttl:
        return payload
    with _CAPTURE_LOCK:
        stamp, payload = _CAPTURE_CACHE
        if payload is not None and time.time() - stamp < ttl:
            return payload
        payload = capture()
        _CAPTURE_CACHE = (time.time(), payload)
        return payload


def capture() -> dict:
    overview = market.get_overview()
    emotion = market.get_short_term_emotion()
    trade_date = _date((overview.get("sentiment") or {}).get("date")) or _date(emotion.get("date")) or str(datetime.now(CN).date())
    prior = market_store.latest_snapshot("daily_review", "vibe", "", trade_date) if market_store.enabled() else None
    prior_activity = (prior or {}).get("payload", {}).get("activity", {})
    if isinstance(prior_activity, dict):
        prior_activity = _canonical_activity(prior_activity)
    current_activity = _canonical_activity(overview.get("activity", {}))
    activity = {**prior_activity, **current_activity} if isinstance(prior_activity, dict) else current_activity
    payload = {
        "date": trade_date,
        "historical": False,
        "indices": astock.index_quote(),
        "global_indices": market.get_global_indices(),
        "overview": overview,
        "emotion": emotion,
        "turnover": market.get_turnover_top(),
        "activity": activity,
        "collected_at": datetime.now(CN).isoformat(),
    }
    if market_store.enabled():
        market_store.save_snapshot("daily_review", "vibe", "", trade_date, 0, payload)
    return payload


def get(trade_date: str = "") -> dict | None:
    if not trade_date:
        if is_trade_date():
            return capture_cached()
        if market_store.enabled():
            hit = market_store.latest_snapshot("daily_review", "vibe", "")
            if hit:
                payload = hit["payload"]
                payload["historical"] = False
                payload.setdefault("collected_at", str(hit.get("collected_at") or ""))
                return payload
        return capture()
    hit = market_store.latest_snapshot("daily_review", "vibe", "", trade_date)
    if hit:
        payload = hit["payload"]
        payload["historical"] = True
        payload.setdefault("collected_at", str(hit.get("collected_at") or ""))
        return payload
    return None


def dates() -> list[str]:
    return market_store.available_dates("daily_review", "vibe", "")
