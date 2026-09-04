from __future__ import annotations

from config.client_config import ClientConfig
from agents.insight import insight_agent


class _Cursor:
    def __init__(self, rows_by_query: dict[str, list[dict]]):
        self.rows_by_query = rows_by_query
        self.description = []
        self.rows = []
        self.queries: list[str] = []

    def execute(self, query: str) -> None:
        self.queries.append(query)
        if query.startswith("DESCRIBE TABLE"):
            table = query.rsplit(".", 1)[-1]
            if table not in self.rows_by_query:
                raise RuntimeError(f"missing table: {table}")
            self.description = [("col_name",)]
            self.rows = [("ok",)]
            return
        for marker, rows in self.rows_by_query.items():
            if marker in query:
                self.description = [(key,) for key in rows[0].keys()] if rows else []
                self.rows = [tuple(row.values()) for row in rows]
                return
        self.description = []
        self.rows = []

    def fetchall(self):
        return self.rows

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self) -> None:
        pass


def _config() -> ClientConfig:
    return ClientConfig(
        client_id="acme",
        client_name="Acme",
        databricks_schema="workspace.attribution_acme",
        attribution_model="last_touch",
    )


def test_fetch_channel_performance_falls_back_to_selected_model_results(monkeypatch):
    cursor = _Cursor(
        {
            "attribution_results": [
                {
                    "report_month": "2026-08",
                    "channel": "Meta",
                    "deals_count": 0.0,
                    "pipeline_value": 0.0,
                    "avg_deal_value": 0.0,
                    "avg_days_to_close": None,
                    "total_spend": 119.4,
                    "roi": 0.0,
                    "cost_per_deal": None,
                    "ingested_at": "2026-08-12",
                    "collected_revenue": 0.0,
                    "refund_rate": 0.0,
                    "true_roi": 0.0,
                    "ltv_90day": 0.0,
                    "analytics_state": "attribution_results",
                }
            ],
        }
    )
    monkeypatch.setattr(
        insight_agent, "_connect_databricks", lambda: _Connection(cursor)
    )

    rows = insight_agent._fetch_channel_performance(_config(), "linear")

    assert rows[0]["analytics_state"] == "attribution_results"
    assert rows[0]["total_spend"] == 119.4
    assert "attribution_model = 'linear'" in " ".join(cursor.queries)


def test_fetch_channel_performance_falls_back_to_spend_only_rows(monkeypatch):
    cursor = _Cursor(
        {
            "ad_spend_normalized": [
                {
                    "report_month": "2026-08",
                    "channel": "Paid Social",
                    "deals_count": 0.0,
                    "pipeline_value": 0.0,
                    "avg_deal_value": 0.0,
                    "avg_days_to_close": None,
                    "total_spend": 119.4,
                    "roi": 0.0,
                    "cost_per_deal": None,
                    "ingested_at": "2026-08-12",
                    "collected_revenue": 0.0,
                    "refund_rate": 0.0,
                    "true_roi": 0.0,
                    "ltv_90day": 0.0,
                    "analytics_state": "ad_activity_without_closed_revenue",
                }
            ],
        }
    )
    monkeypatch.setattr(
        insight_agent, "_connect_databricks", lambda: _Connection(cursor)
    )

    rows = insight_agent._fetch_channel_performance(_config(), "w_shape")

    assert rows[0]["analytics_state"] == "ad_activity_without_closed_revenue"
    assert rows[0]["pipeline_value"] == 0.0
    assert rows[0]["total_spend"] == 119.4
    assert "DATE_FORMAT(MAX(date), 'yyyy-MM')" in " ".join(cursor.queries)


def test_channel_performance_reads_only_latest_report_month(monkeypatch):
    cursor = _Cursor(
        {
            "channel_performance_v2": [
                {
                    "report_month": "2026-08",
                    "channel": "Paid Search",
                    "pipeline_value": 500.0,
                }
            ]
        }
    )
    monkeypatch.setattr(
        insight_agent, "_connect_databricks", lambda: _Connection(cursor)
    )

    rows = insight_agent._fetch_channel_performance(_config())

    assert rows[0]["report_month"] == "2026-08"
    sql = " ".join(cursor.queries)
    assert "WHERE report_month =" in sql
    assert "SELECT MAX(report_month)" in sql
    assert "WHERE pipeline_value > 0" in sql


def test_empty_warehouse_result_does_not_fall_back_to_stale_python_results(
    monkeypatch,
):
    cursor = _Cursor(
        {
            "channel_performance_v2": [],
            "attribution_results": [
                {
                    "report_month": "2026-01",
                    "channel": "Meta",
                    "pipeline_value": 9999.0,
                }
            ],
        }
    )
    monkeypatch.setattr(
        insight_agent, "_connect_databricks", lambda: _Connection(cursor)
    )

    assert insight_agent._fetch_channel_performance(_config()) == []
    assert not any(
        "FROM workspace.attribution_acme.attribution_results" in query
        for query in cursor.queries
    )


def test_fallback_report_does_not_claim_roi_without_closed_revenue():
    report = insight_agent._build_fallback_report(
        _config(),
        [
            {
                "report_month": "2026-08",
                "channel": "Paid Social",
                "deals_count": 0,
                "pipeline_value": 0.0,
                "total_spend": 119.4,
            }
        ],
        "w_shape",
    )

    assert report["overall_roi"] == 0.0
    assert "should not make revenue or ROI performance claims" in report["narrative"]
    assert "Closed-revenue attribution is pending" in report["key_findings"][-1]
