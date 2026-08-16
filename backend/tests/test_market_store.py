import pytest

import market_store


def test_connection_does_not_report_query_errors_as_connection_failures(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def close(self):
            pass

    class Psycopg:
        @staticmethod
        def connect(*args, **kwargs):
            return Connection()

    monkeypatch.setattr(market_store, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(market_store, "_driver", lambda: (Psycopg, object()))

    with pytest.raises(ValueError, match="invalid collector status"):
        with market_store.connection():
            raise ValueError("invalid collector status")


def test_collector_log_schema_allows_partial_status():
    assert "'running', 'ok', 'partial', 'fail', 'skipped'" in market_store.SCHEMA
    assert "collector_logs_status_check" in market_store.SCHEMA_MIGRATIONS[4]
