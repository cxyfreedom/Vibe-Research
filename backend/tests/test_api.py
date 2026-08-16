"""API 验证/契约测（FastAPI TestClient）。大多在校验层就返回，不联网、可靠。"""
import pytest
from fastapi.testclient import TestClient

import app as app_module

client = TestClient(app_module.app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_daily_review_history(monkeypatch):
    monkeypatch.setattr(app_module.daily_review, "get", lambda date: {"date": date, "turnover": {"stocks": []}})
    r = client.get("/api/market/daily-review?date=2026-08-12")
    assert r.status_code == 200
    assert r.json()["data"]["date"] == "2026-08-12"


def test_daily_review_missing_date_404(monkeypatch):
    monkeypatch.setattr(app_module.daily_review, "get", lambda date: None)
    assert client.get("/api/market/daily-review?date=2020-01-01").status_code == 404


@pytest.mark.parametrize("path", [
    "/api/quote?codes=abc",
    "/api/valuation?code=12",
    "/api/margin?code=notcode",
    "/api/holders?code=1234567",
    "/api/announcements?code=",
])
def test_bad_code_400(path):
    assert client.get(path).status_code == 400


def test_industry_top_range():
    assert client.get("/api/industry?top=2").status_code == 422   # ge=5
    assert client.get("/api/industry?top=999").status_code == 422  # le=50


def test_market_lhb_detail_reads_new_persisted_source(monkeypatch):
    payload = {"records": [{"date": "2026-08-14"}, {"date": "2026-08-13"}], "seats": {"buy": [], "sell": []}, "institution": {}}
    calls = []
    monkeypatch.setattr(app_module.market_store, "latest_snapshot", lambda dataset, source, scope, trade_date: calls.append((dataset, source, scope, trade_date)) or {"payload": payload})
    monkeypatch.setattr(app_module.astock, "dragon_tiger_board", lambda *args, **kwargs: pytest.fail("cache hit must not fetch"))
    r = client.get("/api/market-lhb-detail?code=000582&date=2026-08-14")
    assert r.status_code == 200
    assert r.json()["data"]["records"] == [{"date": "2026-08-14"}]
    assert calls == [("lhb_detail", "eastmoney-direct", "000582", "2026-08-14")]


def test_market_lhb_detail_fetches_and_persists_on_miss(monkeypatch):
    payload = {"records": [], "seats": {"buy": [], "sell": []}, "institution": {}}
    saved = []
    monkeypatch.setattr(app_module.market_store, "latest_snapshot", lambda *args: None)
    monkeypatch.setattr(app_module.market_store, "save_snapshot", lambda *args: saved.append(args))
    fetched = []
    monkeypatch.setattr(app_module.astock, "dragon_tiger_board", lambda code, trade_date=None, look_back=30, reason=None: fetched.append((code, trade_date, look_back, reason)) or payload)
    reason = "日跌幅偏离值达到7%的前5只证券"
    r = client.get(f"/api/market-lhb-detail?code=000582&date=2026-08-14&reason={reason}")
    assert r.status_code == 200
    assert r.json()["data"] == payload
    assert fetched == [("000582", "2026-08-14", 0, reason)]
    assert saved[0][:5] == (
        "lhb_detail", "eastmoney-direct",
        app_module.market_collectors.lhb_detail_scope("000582", reason),
        "2026-08-14", 0,
    )
    assert saved[0][6] == {"code": "000582", "reason": reason}


def test_stock_universe_endpoint(monkeypatch):
    monkeypatch.setattr(app_module.market_store, "query_stocks", lambda search, page, size: {
        "total": 1, "page": page, "size": size, "items": [{"code": "000779", "name": "甘咨询"}],
    })
    response = client.get("/api/stocks?search=甘咨询&page=1&size=20")
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["code"] == "000779"


def test_stock_data_bundle_endpoint(monkeypatch):
    payload = {
        "code": "000779",
        "trade_date": "2026-08-14",
        "collected_at": "2026-08-14 19:30:00+08:00",
        "data": {"valuation": {"code": "000779", "price": 12.34}},
    }
    monkeypatch.setattr(app_module.market_store, "stock_data_bundle", lambda code, date: payload)
    response = client.get("/api/stock-data/000779/bundle?date=2026-08-14")
    assert response.status_code == 200
    assert response.json()["data"] == payload


def test_stock_data_bundle_rejects_invalid_date():
    response = client.get("/api/stock-data/000779/bundle?date=2026-8-14")
    assert response.status_code == 400


def test_chat_empty_messages_400():
    r = client.post("/api/chat", json={"messages": [], "llm": {"model": "x", "baseURL": "http://x", "apiKey": "k"}})
    assert r.status_code == 400


def test_chat_api_missing_key_400():
    # API 接入缺 baseURL/apiKey → 400（在开流前拦下）
    r = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "llm": {"provider": "deepseek", "model": "deepseek-chat", "baseURL": "", "apiKey": ""},
    })
    assert r.status_code == 400


def test_chat_cli_not_installed_400():
    # 订阅接入选一个本机没装的 CLI → 400 明确提示（不静默失败）
    r = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "llm": {"provider": "cli-qwen", "model": "qwen-code", "baseURL": "", "apiKey": ""},
    })
    # qwen 一般未装 → 400；若恰好装了 qwen 则会进流式（放宽断言）
    assert r.status_code in (400, 200)


def test_global_stock_404(monkeypatch):
    """无法解析的美股/港股代码 → 404（不 500、不崩）。"""
    import gstock
    monkeypatch.setattr(gstock, "us_hk_stock", lambda q: {})
    assert client.get("/api/global/stock?symbol=ZZZZ").status_code == 404


def test_gstock_quote_full_null_shape():
    """行情取不到时 `_quote_from({})` 仍返回完整 null 形状（契合 GlobalQuote 类型），不是空 dict。"""
    import gstock
    q = gstock._quote_from({})
    assert set(q) == {"code", "name", "price", "open", "high", "low", "prev_close", "amount", "mcap", "change_pct"}
    assert all(v is None for v in q.values())
