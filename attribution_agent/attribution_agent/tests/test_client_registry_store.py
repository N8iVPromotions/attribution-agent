"""Tests for the client registry storage backends (local JSON vs Delta)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import client_config
from config.client_config import ClientConfig


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Point the local registry at a temp file and reset caches around each test."""
    monkeypatch.setattr(
        client_config, "CLIENT_REGISTRY_PATH", tmp_path / "registry.json"
    )
    monkeypatch.delenv("ATTRIBUTION_CLIENT_REGISTRY_BACKEND", raising=False)
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
    client_config._invalidate_registry_cache()
    yield
    client_config._invalidate_registry_cache()
    client_config.CLIENT_REGISTRY.clear()
    client_config.CLIENT_REGISTRY.update(client_config.BASE_CLIENT_REGISTRY)


def _sample_config(client_id: str = "acme_co") -> ClientConfig:
    return ClientConfig(
        client_id=client_id,
        client_name="Acme Co",
        meta_enabled=True,
        meta_ad_account_id="act_1",
        databricks_schema="workspace.attribution_acme_co",
    )


class _FakeDeltaStore:
    """In-memory stand-in for the client_registry Delta table."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def fetch(self):
        return [
            {"client_id": cid, "config_json": row["config_json"]}
            for cid, row in self.rows.items()
            if row["is_active"]
        ]

    def upsert(self, client_id, config_json, is_active=True):
        self.rows[client_id] = {
            "config_json": config_json,
            "is_active": is_active,
        }


@pytest.fixture
def fake_delta(monkeypatch):
    store = _FakeDeltaStore()
    import utils.databricks_writer as writer

    monkeypatch.setattr(writer, "fetch_client_registry_rows", store.fetch)
    monkeypatch.setattr(writer, "upsert_client_registry_entry", store.upsert)
    monkeypatch.setenv("ATTRIBUTION_CLIENT_REGISTRY_BACKEND", "delta")
    return store


# ── Backend selection ─────────────────────────────────────────────────────────


def test_backend_defaults_to_local(monkeypatch):
    assert client_config._registry_backend() == "local"


@pytest.mark.parametrize(
    "env_key",
    ["DATABRICKS_RUNTIME_VERSION", "DATABRICKS_APP_PORT", "DATABRICKS_CLIENT_ID"],
)
def test_backend_auto_detects_databricks(monkeypatch, env_key):
    monkeypatch.setenv(env_key, "something")
    assert client_config._registry_backend() == "delta"


def test_backend_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8080")
    monkeypatch.setenv("ATTRIBUTION_CLIENT_REGISTRY_BACKEND", "local")
    assert client_config._registry_backend() == "local"


# ── Local backend (unchanged behavior) ────────────────────────────────────────


def test_local_save_and_reload_round_trip():
    client_config.save_client_config(_sample_config())
    registry = client_config.reload_client_registry()
    assert "acme_co" in registry
    assert registry["acme_co"].client_name == "Acme Co"
    assert client_config.is_custom_client("acme_co")

    client_config.delete_client_config("acme_co")
    assert "acme_co" not in client_config.reload_client_registry()


# ── Delta backend ─────────────────────────────────────────────────────────────


def test_delta_save_and_reload_round_trip(fake_delta):
    client_config.save_client_config(_sample_config())

    assert "acme_co" in fake_delta.rows
    stored = json.loads(fake_delta.rows["acme_co"]["config_json"])
    assert stored["client_name"] == "Acme Co"

    client_config._invalidate_registry_cache()
    registry = client_config.reload_client_registry()
    assert registry["acme_co"].meta_ad_account_id == "act_1"
    assert client_config.is_custom_client("acme_co")


def test_delta_delete_is_soft(fake_delta):
    client_config.save_client_config(_sample_config())
    client_config.delete_client_config("acme_co")

    # Row survives for audit but is inactive and gone from the registry.
    assert fake_delta.rows["acme_co"]["is_active"] is False
    assert "acme_co" not in client_config.reload_client_registry()


def test_delta_delete_ignores_unknown_client(fake_delta):
    client_config.delete_client_config("never_saved")
    assert "never_saved" not in fake_delta.rows


def test_delta_read_failure_falls_back_to_local_json(monkeypatch, tmp_path):
    import utils.databricks_writer as writer

    def _boom():
        raise ConnectionError("warehouse unreachable")

    monkeypatch.setattr(writer, "fetch_client_registry_rows", _boom)
    monkeypatch.setenv("ATTRIBUTION_CLIENT_REGISTRY_BACKEND", "delta")

    local_path = Path(client_config.CLIENT_REGISTRY_PATH)
    local_path.write_text(
        json.dumps({"clients": {"json_client": {"client_name": "Json Client"}}}),
        encoding="utf-8",
    )
    client_config._invalidate_registry_cache()
    registry = client_config.reload_client_registry()
    assert "json_client" in registry


def test_delta_write_failure_propagates(fake_delta, monkeypatch):
    import utils.databricks_writer as writer

    def _boom(*args, **kwargs):
        raise ConnectionError("warehouse unreachable")

    monkeypatch.setattr(writer, "upsert_client_registry_entry", _boom)
    with pytest.raises(ConnectionError):
        client_config.save_client_config(_sample_config())


def test_delta_reads_are_cached(fake_delta, monkeypatch):
    calls = {"n": 0}
    real_fetch = fake_delta.fetch

    def _counting_fetch():
        calls["n"] += 1
        return real_fetch()

    import utils.databricks_writer as writer

    monkeypatch.setattr(writer, "fetch_client_registry_rows", _counting_fetch)
    client_config._invalidate_registry_cache()
    client_config.reload_client_registry()
    client_config.reload_client_registry()
    client_config.is_custom_client("anything")
    assert calls["n"] == 1
