"""Credentials passed as URL query params must never reach logs or the
source_failures record — requests copies the full URL into exception text."""

from __future__ import annotations

import pytest
import requests

from agents.ingest.meta_connector import MetaConnector
from utils.secrets import redact_secrets, resolve_secret, secret_id_for_client


class TestRedactSecrets:
    def test_redacts_access_token(self):
        msg = (
            "400 Client Error: Bad Request for url: "
            "https://graph.facebook.com/v19.0/act_123/insights?"
            "fields=spend&access_token=EAABsbCS1234SECRET&limit=500"
        )
        out = redact_secrets(msg)
        assert "EAABsbCS1234SECRET" not in out
        assert "access_token=REDACTED" in out
        # Non-secret params survive
        assert "fields=spend" in out
        assert "limit=500" in out

    def test_redacts_other_credential_params_case_insensitive(self):
        out = redact_secrets(
            "url?API_KEY=abc123&client_secret=shh&refresh_token=r-1//tok end"
        )
        assert "abc123" not in out
        assert "shh" not in out
        assert "r-1//tok" not in out
        assert out.endswith(" end")

    def test_plain_text_unchanged(self):
        assert redact_secrets("connection reset by peer") == (
            "connection reset by peer"
        )


class TestSecretReferences:
    def test_secret_id_for_client_is_stable(self, monkeypatch):
        monkeypatch.setenv("ATTRIBUTION_ENV", "pilot")
        assert (
            secret_id_for_client("Acme Co!", "Meta Access Token")
            == "attr-pilot-acme-co-meta-access-token"
        )

    def test_resolve_secret_falls_back_to_env_without_gcp_project(self, monkeypatch):
        for key in (
            "GOOGLE_CLOUD_PROJECT",
            "GCP_PROJECT",
            "PROJECT_ID",
            "CLOUDSDK_CORE_PROJECT",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("META_ACCESS_TOKEN", "env-token\r\n")

        assert (
            resolve_secret("attr-prod-acme-meta-access-token", "META_ACCESS_TOKEN")
            == "env-token"
        )


class TestMetaConnectorRedaction:
    def test_http_error_message_has_no_token(self, monkeypatch):
        connector = MetaConnector(access_token="EAABtopsecrettoken")

        def fake_get(url, params=None, timeout=None):
            resp = requests.Response()
            resp.status_code = 400
            resp.url = f"{url}?access_token={params['access_token']}&limit=500"
            return resp

        monkeypatch.setattr(connector.session, "get", fake_get)
        # __wrapped__ skips the tenacity retry (and its backoff sleeps);
        # redaction happens inside _get itself.
        with pytest.raises(requests.HTTPError) as excinfo:
            MetaConnector._get.__wrapped__(
                connector, "https://graph.facebook.com/v19.0/act_1/insights", {}
            )
        assert "EAABtopsecrettoken" not in str(excinfo.value)
        assert "access_token=REDACTED" in str(excinfo.value)

    def test_http_error_message_includes_redacted_meta_body(self, monkeypatch):
        connector = MetaConnector(access_token="EAABtopsecrettoken")

        def fake_get(url, params=None, timeout=None):
            resp = requests.Response()
            resp.status_code = 400
            resp.url = f"{url}?access_token={params['access_token']}&limit=500"
            resp._content = b'{"error":{"message":"Session has expired","code":190}}'
            return resp

        monkeypatch.setattr(connector.session, "get", fake_get)

        with pytest.raises(requests.HTTPError) as excinfo:
            MetaConnector._get.__wrapped__(
                connector, "https://graph.facebook.com/v19.0/act_1/insights", {}
            )

        message = str(excinfo.value)
        assert "Session has expired" in message
        assert "EAABtopsecrettoken" not in message
        assert "access_token=REDACTED" in message
