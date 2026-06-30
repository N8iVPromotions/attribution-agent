"""
tests/test_ingest_isolation.py
------------------------------
Per-source failure isolation in the ingest flow, and LinkedIn pagination.

These exercise the hardening that lets the run survive a single bad data source
and pull every page from LinkedIn — without live credentials.
"""

from __future__ import annotations

import concurrent.futures

import pandas as pd

from flows.ingest_flow import _collect


def _resolved(value):
    """A future already resolved to `value`."""
    fut = concurrent.futures.Future()
    fut.set_result(value)
    return fut


def _failed(exc):
    fut = concurrent.futures.Future()
    fut.set_exception(exc)
    return fut


def test_collect_returns_value_on_success():
    failures = {}
    df = pd.DataFrame([{"a": 1}])
    out = _collect(_resolved(df), "pull-meta", failures)
    assert out is df
    assert failures == {}


def test_collect_isolates_failure():
    failures = {}
    out = _collect(
        _failed(RuntimeError("token expired")), "pull-linkedin-ads", failures
    )
    assert out is None
    assert "pull-linkedin-ads" in failures
    assert "token expired" in failures["pull-linkedin-ads"]


def test_collect_one_failure_does_not_affect_others():
    failures = {}
    good = _collect(_resolved(pd.DataFrame([{"x": 1}])), "pull-meta", failures)
    bad = _collect(_failed(ValueError("boom")), "pull-stripe", failures)
    assert good is not None
    assert bad is None
    assert list(failures.keys()) == ["pull-stripe"]


# ── LinkedIn pagination ─────────────────────────────────────────────────────────


def test_linkedin_paginates_until_short_page(monkeypatch):
    from agents.ingest import linkedin_connector as lc

    monkeypatch.setattr(lc, "PAGE_SIZE", 2)

    pages = [
        {"elements": [_li_elem(1), _li_elem(2)]},  # full page → keep going
        {"elements": [_li_elem(3)]},  # short page → stop
    ]
    seen_starts = []

    def _fake_get(url, headers, params):
        seen_starts.append(params["start"])
        return pages[len(seen_starts) - 1]

    monkeypatch.setattr(lc, "_get", _fake_get)

    df = lc.pull_linkedin_ads_data(
        account_id="123", lookback_days=7, access_token="tok"
    )
    assert len(df) == 3
    assert seen_starts == [0, 2]  # second page requested at offset PAGE_SIZE


def _li_elem(day: int) -> dict:
    return {
        "dateRange": {"start": {"year": 2026, "month": 6, "day": day}},
        "pivotValues": ["urn:li:sponsoredCampaign:999"],
        "impressions": 10,
        "clicks": 1,
        "costInLocalCurrency": "5.0",
        "externalWebsiteConversions": 1,
    }
