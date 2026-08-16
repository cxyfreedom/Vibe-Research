from datetime import datetime

import market_runtime


def test_trading_hours_exclude_lunch_and_weekends():
    assert market_runtime._trading(datetime(2026, 8, 14, 9, 30))
    assert not market_runtime._trading(datetime(2026, 8, 14, 12, 0))
    assert market_runtime._trading(datetime(2026, 8, 14, 13, 0))
    assert not market_runtime._trading(datetime(2026, 8, 16, 10, 0))


def test_status_exposes_market_schedule():
    schedule = market_runtime.status()
    assert schedule["intervals"]["news"] == 60
    assert "activity" not in schedule["intervals"]
    assert "activity" not in market_runtime.market_collectors.JOBS
    assert schedule["intervals"]["hot"] == 1800
    assert schedule["intervals"]["fund_flow"] == 60
    assert schedule["intervals"]["boards_realtime"] == 60
    assert "tech_rank" in schedule["daily"]["15:05"]
    assert schedule["daily"]["16:00"] == "retry_missing"
    assert schedule["daily"]["18:45"] == ["lhb_detail"]
    assert schedule["daily"]["19:00"] == ["stock_archive"]
    assert schedule["daily"]["21:00"] == ["stock_archive_retry"]
    assert schedule["daily"]["startup"] == ["trade_calendar"]


def test_periodic_jobs_align_to_wall_clock_buckets():
    assert not market_runtime._interval_due(10 * 60 + 59, 10 * 60 + 1, 60)
    assert market_runtime._interval_due(11 * 60, 10 * 60 + 59, 60)
    assert market_runtime._interval_due(10 * 1800, 9 * 1800 + 1799, 1800)
