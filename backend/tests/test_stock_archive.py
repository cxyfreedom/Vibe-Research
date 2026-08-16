from datetime import datetime

import market_collectors
import market_runtime
import stock_archive


def test_stock_read_through_uses_daily_database_snapshot(monkeypatch):
    monkeypatch.setattr(stock_archive.store, "enabled", lambda: True)
    monkeypatch.setattr(stock_archive.store, "latest_stock_data", lambda code, kind, day: {
        "payload": {"code": code, "price": 10},
    })
    result = stock_archive.read_through(
        "000779", "valuation", lambda: (_ for _ in ()).throw(AssertionError("must not fetch")),
        "2026-08-14",
    )
    assert result["price"] == 10


def test_stock_read_through_persists_cache_miss(monkeypatch):
    saved = []
    monkeypatch.setattr(stock_archive.store, "enabled", lambda: True)
    monkeypatch.setattr(stock_archive.store, "latest_stock_data", lambda *args: None)
    monkeypatch.setattr(stock_archive.store, "save_stock_bundle", lambda *args: saved.append(args))
    result = stock_archive.read_through("000779", "holders", lambda: [{"holder_num": 1}], "2026-08-14")
    assert result == [{"holder_num": 1}]
    assert saved == [("000779", "2026-08-14", {"holders": result})]


def test_trade_date_uses_persisted_exchange_calendar(monkeypatch):
    monkeypatch.setattr(market_collectors.store, "enabled", lambda: True)
    monkeypatch.setattr(
        market_collectors.store, "is_trading_day",
        lambda day: {"2026-10-09": True}.get(str(day), False),
    )
    assert market_collectors._trade_date(datetime(2026, 10, 10, 10, 0)) == "2026-10-09"


def test_runtime_excludes_weekday_exchange_holiday(monkeypatch):
    monkeypatch.setattr(market_runtime.market_store, "enabled", lambda: True)
    monkeypatch.setattr(market_runtime.market_store, "is_trading_day", lambda day: False)
    assert not market_runtime._trading(datetime(2026, 10, 1, 10, 0))


def test_all_market_fund_flow_batch_marks_uncovered_stocks(monkeypatch):
    saved = []
    monkeypatch.setattr(stock_archive.astock, "all_stock_fund_flow_snapshot", lambda day: {
        "000001": [{"date": day, "main_net": 1}],
    })
    monkeypatch.setattr(stock_archive.store, "save_stock_dataset_batch", lambda *args, **kwargs: saved.append((args, kwargs)))
    completed, failed = stock_archive._collect_fund_flows(
        ["000001", "000002"], "2026-08-14", False, set(),
    )
    assert (completed, failed) == (1, 1)
    assert saved[0][0][2] == {"000001": [{"date": "2026-08-14", "main_net": 1}]}
    assert saved[0][0][3] == ["000002"]
    assert saved[0][1]["source"] == "ths"
