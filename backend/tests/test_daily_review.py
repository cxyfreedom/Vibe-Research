import daily_review
import market


def test_chinese_activity_fields_get_stable_persisted_keys():
    result = daily_review._canonical_activity({"上涨": 12, "下跌": 8, "活跃度": "60.00%", "停牌": 2, "median_rise": "legacy"})
    assert result["up_count"] == 12
    assert result["down_count"] == 8
    assert result["activity"] == "60.00"
    assert result["paused_count"] == 2
    assert "median_rise" not in result


def test_latest_review_reads_database_without_live_capture(monkeypatch):
    monkeypatch.setattr(daily_review, "is_trade_date", lambda: False)
    monkeypatch.setattr(daily_review.market_store, "enabled", lambda: True)
    monkeypatch.setattr(daily_review.market_store, "latest_snapshot", lambda *args: {
        "payload": {"date": "2026-08-13"}, "collected_at": "2026-08-13T15:00:00+08:00",
    })
    monkeypatch.setattr(daily_review, "capture", lambda: (_ for _ in ()).throw(AssertionError("must not fetch live data")))
    result = daily_review.get()
    assert result["date"] == "2026-08-13"
    assert result["historical"] is False


def test_latest_review_captures_live_on_trade_date(monkeypatch):
    monkeypatch.setattr(daily_review, "is_trade_date", lambda: True)
    monkeypatch.setattr(daily_review, "capture_cached", lambda: {"date": "live"})
    assert daily_review.get() == {"date": "live"}


def test_weekend_is_not_trade_date():
    assert daily_review.is_trade_date(daily_review.date_type(2026, 8, 16)) is False


def test_turnover_rank_uses_top_50(monkeypatch):
    market._CACHE.clear()
    seen = []
    monkeypatch.setattr(market.astock, "market_turnover_rank", lambda count: seen.append(count) or [])
    market.get_turnover_top()
    assert seen == [50]
