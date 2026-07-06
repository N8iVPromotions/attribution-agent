"""
demo_store.py — mock database for the customer-facing demo environment.

Implements the three-table schema from the Unified Strategic Playbook
(docs/DEMO_PLAYBOOK.md, section 3) on top of SQLite so the demo is fully
self-contained: no Databricks, no live APIs, no credentials.

Tables:
  arie_click_logs     anon_id, utm_source, utm_campaign, channel, page, occurred_at
  arie_identity_map   anon_id, resolved_email, updated_at
  crm_deal_events     email, deal_amount, status, occurred_at

The store is intentionally dumb — inserts and reads only. All closed-loop
matching logic lives in demo_engine.py so it can be unit tested against an
in-memory database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS arie_click_logs (
    click_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    anon_id      TEXT NOT NULL,
    utm_source   TEXT NOT NULL,
    utm_campaign TEXT NOT NULL,
    channel      TEXT NOT NULL,
    page         TEXT NOT NULL DEFAULT '',
    occurred_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS arie_identity_map (
    anon_id        TEXT PRIMARY KEY,
    resolved_email TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crm_deal_events (
    deal_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    deal_amount REAL NOT NULL,
    status      TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""

_TABLES = ("arie_click_logs", "arie_identity_map", "crm_deal_events")


@dataclass(frozen=True)
class ClickLog:
    click_id: int
    anon_id: str
    utm_source: str
    utm_campaign: str
    channel: str
    page: str
    occurred_at: datetime


@dataclass(frozen=True)
class IdentityLink:
    anon_id: str
    resolved_email: str
    updated_at: datetime


@dataclass(frozen=True)
class DealEvent:
    deal_id: int
    email: str
    deal_amount: float
    status: str
    occurred_at: datetime


class DemoStore:
    """SQLite-backed mock of the attribution data layer for demos."""

    def __init__(self, db_path: str = ":memory:") -> None:
        # check_same_thread=False lets Streamlit reruns (different threads)
        # reuse the connection held in session state.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── writes ────────────────────────────────────────────────

    def insert_click(
        self,
        *,
        anon_id: str,
        utm_source: str,
        utm_campaign: str,
        channel: str,
        page: str = "",
        occurred_at: datetime,
    ) -> None:
        self._conn.execute(
            "INSERT INTO arie_click_logs "
            "(anon_id, utm_source, utm_campaign, channel, page, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (anon_id, utm_source, utm_campaign, channel, page, occurred_at.isoformat()),
        )
        self._conn.commit()

    def upsert_identity(
        self, *, anon_id: str, resolved_email: str, updated_at: datetime
    ) -> None:
        self._conn.execute(
            "INSERT INTO arie_identity_map (anon_id, resolved_email, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(anon_id) DO UPDATE SET "
            "resolved_email = excluded.resolved_email, "
            "updated_at = excluded.updated_at",
            (anon_id, resolved_email.strip().lower(), updated_at.isoformat()),
        )
        self._conn.commit()

    def insert_deal_event(
        self, *, email: str, deal_amount: float, status: str, occurred_at: datetime
    ) -> None:
        self._conn.execute(
            "INSERT INTO crm_deal_events (email, deal_amount, status, occurred_at) "
            "VALUES (?, ?, ?, ?)",
            (email.strip().lower(), deal_amount, status, occurred_at.isoformat()),
        )
        self._conn.commit()

    def reset(self) -> None:
        for table in _TABLES:
            self._conn.execute(f"DELETE FROM {table}")
        self._conn.commit()

    # ── reads ─────────────────────────────────────────────────

    def click_logs(self, anon_ids: list[str] | None = None) -> list[ClickLog]:
        query = (
            "SELECT click_id, anon_id, utm_source, utm_campaign, channel, page, "
            "occurred_at FROM arie_click_logs"
        )
        params: tuple = ()
        if anon_ids is not None:
            if not anon_ids:
                return []
            placeholders = ", ".join("?" for _ in anon_ids)
            query += f" WHERE anon_id IN ({placeholders})"
            params = tuple(anon_ids)
        query += " ORDER BY occurred_at, click_id"
        rows = self._conn.execute(query, params).fetchall()
        return [
            ClickLog(
                click_id=row[0],
                anon_id=row[1],
                utm_source=row[2],
                utm_campaign=row[3],
                channel=row[4],
                page=row[5],
                occurred_at=datetime.fromisoformat(row[6]),
            )
            for row in rows
        ]

    def identity_links(self, email: str | None = None) -> list[IdentityLink]:
        query = "SELECT anon_id, resolved_email, updated_at FROM arie_identity_map"
        params: tuple = ()
        if email is not None:
            query += " WHERE resolved_email = ?"
            params = (email.strip().lower(),)
        query += " ORDER BY updated_at, anon_id"
        rows = self._conn.execute(query, params).fetchall()
        return [
            IdentityLink(
                anon_id=row[0],
                resolved_email=row[1],
                updated_at=datetime.fromisoformat(row[2]),
            )
            for row in rows
        ]

    def deal_events(self, email: str | None = None) -> list[DealEvent]:
        query = "SELECT deal_id, email, deal_amount, status, occurred_at FROM crm_deal_events"
        params: tuple = ()
        if email is not None:
            query += " WHERE email = ?"
            params = (email.strip().lower(),)
        query += " ORDER BY occurred_at, deal_id"
        rows = self._conn.execute(query, params).fetchall()
        return [
            DealEvent(
                deal_id=row[0],
                email=row[1],
                deal_amount=row[2],
                status=row[3],
                occurred_at=datetime.fromisoformat(row[4]),
            )
            for row in rows
        ]
