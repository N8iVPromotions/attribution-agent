from __future__ import annotations

import pandas as pd

from utils import databricks_writer as db


def test_sql_upsert_uses_unique_staging_table(monkeypatch):
    cursor = _FakeCursor()
    monkeypatch.setattr(db, "_is_databricks", lambda: False)
    monkeypatch.setattr(db, "_get_connection", lambda: _FakeConnection(cursor))

    count = db._upsert_dataframe(
        pd.DataFrame([{"user_id": "u1", "email": "zajen@n8ivpromotions.com"}]),
        "workspace.attribution_ops",
        "auth_users",
        ["user_id"],
    )

    assert count == 1
    sql = "\n".join(cursor.execute_calls)
    assert "CREATE OR REPLACE TABLE" not in sql
    assert "workspace.attribution_ops.auth_users_staging_" in sql
    assert "DROP TABLE IF EXISTS workspace.attribution_ops.auth_users_staging_" in sql
    assert cursor.closed
    assert cursor.connection.closed


def test_sql_upsert_retries_delta_concurrency_conflict(monkeypatch):
    cursors = [_FakeCursor(fail_merge=True), _FakeCursor()]
    monkeypatch.setattr(db, "_is_databricks", lambda: False)
    monkeypatch.setattr(db, "_get_connection", lambda: _FakeConnection(cursors.pop(0)))
    monkeypatch.setattr(db.time, "sleep", lambda _seconds: None)

    count = db._upsert_dataframe(
        pd.DataFrame([{"session_id": "s1", "email": "zajen@n8ivpromotions.com"}]),
        "workspace.attribution_ops",
        "auth_sessions",
        ["session_id"],
    )

    assert count == 1
    assert len(cursors) == 0


def test_sql_upsert_includes_target_pruning_predicate(monkeypatch):
    cursor = _FakeCursor()
    monkeypatch.setattr(db, "_is_databricks", lambda: False)
    monkeypatch.setattr(db, "_get_connection", lambda: _FakeConnection(cursor))

    db._upsert_dataframe(
        pd.DataFrame([{"campaign_id": "c1", "date": "2026-08-01"}]),
        "workspace.client",
        "meta_ads_raw",
        ["campaign_id", "date"],
        target_predicate="t.date BETWEEN DATE '2026-08-01' AND DATE '2026-08-01'",
    )

    merge_sql = next(sql for sql in cursor.execute_calls if "MERGE INTO" in sql)
    assert "t.date BETWEEN DATE '2026-08-01' AND DATE '2026-08-01'" in merge_sql


def test_sql_upsert_sends_bounded_batches(monkeypatch):
    cursor = _FakeCursor()
    monkeypatch.setattr(db, "_is_databricks", lambda: False)
    monkeypatch.setattr(db, "_get_connection", lambda: _FakeConnection(cursor))
    frame = pd.DataFrame({"id": [str(index) for index in range(2_001)]})

    db._upsert_dataframe(frame, "workspace.client", "events", ["id"])

    batch_sizes = [len(rows) for _, rows in cursor.executemany_calls]
    assert batch_sizes == [1000, 1000, 1]


class _FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.closed = False
        cursor.connection = self

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


class _FakeCursor:
    def __init__(self, *, fail_merge: bool = False):
        self.execute_calls: list[str] = []
        self.executemany_calls: list[tuple[str, list[tuple]]] = []
        self.fail_merge = fail_merge
        self.closed = False
        self.connection: _FakeConnection | None = None

    def execute(self, sql: str):
        compact_sql = " ".join(sql.split())
        self.execute_calls.append(compact_sql)
        if self.fail_merge and "MERGE INTO" in compact_sql:
            raise RuntimeError(
                "[DELTA_CONCURRENT_APPEND] Transaction conflict detected"
            )

    def executemany(self, sql: str, rows: list[tuple]):
        self.executemany_calls.append((" ".join(sql.split()), rows))

    def close(self):
        self.closed = True
