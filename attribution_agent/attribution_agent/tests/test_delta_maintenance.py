from __future__ import annotations

import pytest

from utils import databricks_writer


def test_liquid_clustering_migration_uses_replace_partitioned_by(monkeypatch):
    statements = []
    monkeypatch.setattr(databricks_writer, "_run_sql", statements.append)

    databricks_writer.apply_liquid_clustering_v2("workspace.attribution_client_a")

    assert len(statements) == 4
    assert all("delta.enableDeletionVectors" in sql for sql in statements[::2])
    assert all(
        "REPLACE PARTITIONED BY WITH CLUSTER BY (date, campaign_id)" in sql
        for sql in statements[1::2]
    )


def test_weekly_maintenance_optimizes_then_vacuums(monkeypatch):
    statements = []
    monkeypatch.setattr(databricks_writer, "_run_sql", statements.append)

    databricks_writer.run_delta_maintenance("workspace.attribution_client_a")

    assert statements == [
        "OPTIMIZE workspace.attribution_client_a.ad_spend_normalized",
        "VACUUM workspace.attribution_client_a.ad_spend_normalized RETAIN 168 HOURS",
        "OPTIMIZE workspace.attribution_client_a.meta_ads_raw",
        "VACUUM workspace.attribution_client_a.meta_ads_raw RETAIN 168 HOURS",
    ]


def test_maintenance_rejects_unsafe_identifiers_and_retention(monkeypatch):
    monkeypatch.setattr(databricks_writer, "_run_sql", lambda _sql: None)
    with pytest.raises(ValueError, match="Unsafe"):
        databricks_writer.run_delta_maintenance("workspace.schema; DROP TABLE x")
    with pytest.raises(ValueError, match="at least 168"):
        databricks_writer.run_delta_maintenance("workspace.schema", vacuum_hours=24)
