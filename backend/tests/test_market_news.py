from datetime import datetime
import json

import astock
import market_collectors
import requests


def test_news_ts_parses_full_datetime():
    ts = market_collectors._news_ts("2026-08-16 15:41:30", 0)
    assert datetime.fromtimestamp(ts, market_collectors.CN).strftime("%Y-%m-%d %H:%M:%S") == "2026-08-16 15:41:30"


def test_news_ts_anchors_time_only_to_today():
    ts = market_collectors._news_ts("15:41:30", 0)
    parsed = datetime.fromtimestamp(ts, market_collectors.CN)
    assert parsed.date() == datetime.now(market_collectors.CN).date()
    assert parsed.strftime("%H:%M:%S") == "15:41:30"


def test_news_ts_falls_back_for_unknown_value():
    assert market_collectors._news_ts("unknown", 123) == 123


def test_eastmoney_hot_rank_keeps_ranking_when_quote_enrichment_fails(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"sc": "SZ000001", "rk": 1}, {"sc": "SH600000", "rk": 2}]}

    class Session:
        trust_env = True

        def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(requests, "Session", Session)
    monkeypatch.setattr(astock, "tencent_quote", lambda codes: (_ for _ in ()).throw(RuntimeError("offline")))

    rows = market_collectors._eastmoney_hot_rank()

    assert [row["当前排名"] for row in rows] == [1, 2]
    assert [row["代码"] for row in rows] == ["SZ000001", "SH600000"]
    assert rows[0]["最新价"] is None


def test_run_marks_source_errors_as_partial(monkeypatch):
    finished = []
    monkeypatch.setattr(market_collectors.store, "start_log", lambda job, source: 7)
    monkeypatch.setattr(
        market_collectors.store, "finish_log",
        lambda log_id, status, detail="": finished.append((log_id, status, detail)),
    )

    result = market_collectors._run(
        "hot", "em,ths,baidu", lambda: {"count": 112, "errors": {"em": "unavailable"}},
    )

    assert result["count"] == 112
    assert finished[0][:2] == (7, "partial")
    assert json.loads(finished[0][2]) == {"count": 112, "errors": {"em": "unavailable"}}


def test_hot_empty_source_is_partial_without_saving_empty_snapshot(monkeypatch):
    class EmptyFrame:
        def __len__(self):
            return 0

    saved = []
    finished = []
    monkeypatch.setattr(market_collectors, "_ak", lambda: type("Ak", (), {
        "stock_hot_search_baidu": staticmethod(lambda **kwargs: EmptyFrame()),
    })())
    monkeypatch.setattr(market_collectors, "_eastmoney_hot_rank", lambda: [{"当前排名": 1}])
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: type("Response", (), {
        "raise_for_status": lambda self: None,
        "json": lambda self: {"data": {"stock_list": [{"order": 1}]}},
    })())
    monkeypatch.setattr(market_collectors.store, "save_snapshot", lambda *args: saved.append(args))
    monkeypatch.setattr(market_collectors.store, "start_log", lambda *args: 8)
    monkeypatch.setattr(
        market_collectors.store, "finish_log",
        lambda log_id, status, detail="": finished.append((log_id, status, detail)),
    )

    result = market_collectors.collect_hot()

    assert result["count"] == 2
    assert result["errors"] == {"baidu": "来源暂不可用：返回空列表"}
    assert [args[1] for args in saved] == ["em", "ths"]
    assert finished[0][1] == "partial"
    assert json.loads(finished[0][2])["errors"] == result["errors"]
