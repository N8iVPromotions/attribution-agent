from __future__ import annotations

import pytest

from utils import idempotency


def test_report_delivery_key_is_deterministic_and_model_scoped():
    assert idempotency.report_delivery_key("client-a", "2026-07", "linear") == (
        "report:client-a:2026-07:linear"
    )
    assert idempotency.report_delivery_key("client-a", "2026-07", "last_touch") != (
        idempotency.report_delivery_key("client-a", "2026-07", "linear")
    )
    exact_key = idempotency.report_delivery_key(
        "client-a",
        "2026-07",
        "last_touch",
        report_id="report-id",
        delivery_config_fingerprint="config-id",
    )
    assert exact_key.endswith(":report-id:config-id")


def test_report_delivery_key_requires_complete_artifact_envelope_pair():
    with pytest.raises(ValueError, match="must be supplied together"):
        idempotency.report_delivery_key(
            "client-a", "2026-07", "last_touch", report_id="report-id"
        )


def test_local_delivery_claim_prevents_duplicates_and_releases_failures(monkeypatch):
    monkeypatch.delenv("ARIE_DELIVERY_IDEMPOTENCY_BUCKET", raising=False)
    idempotency._LOCAL_DELIVERY_KEYS.clear()

    first = idempotency.claim_report_delivery("report:c:2026-07:linear")
    duplicate = idempotency.claim_report_delivery("report:c:2026-07:linear")

    assert first.acquired is True
    assert duplicate.acquired is False
    assert duplicate.state == "claimed"

    first.release()
    retry = idempotency.claim_report_delivery("report:c:2026-07:linear")
    assert retry.acquired is True

    retry.complete()
    sent_duplicate = idempotency.claim_report_delivery("report:c:2026-07:linear")
    assert sent_duplicate.acquired is False
    assert sent_duplicate.state == "sent"


@pytest.mark.parametrize(
    ("payload", "expected_state"),
    [
        ({"key": "delivery-key", "status": "claimed"}, "claimed"),
        ({"key": "delivery-key", "status": "sent"}, "sent"),
        ({"key": "other-key", "status": "sent"}, None),
        ({"key": "delivery-key", "status": "unexpected"}, None),
    ],
)
def test_gcs_duplicate_claim_reads_and_validates_existing_state(
    monkeypatch, payload, expected_state
):
    import json

    from google.api_core.exceptions import PreconditionFailed
    from google.cloud import storage

    class Blob:
        generation = 7

        def upload_from_string(self, *_args, **_kwargs):
            raise PreconditionFailed("already exists")

        def download_as_text(self):
            return json.dumps(payload)

    blob = Blob()

    class Bucket:
        def blob(self, _name):
            return blob

    class Client:
        def bucket(self, _name):
            return Bucket()

    monkeypatch.setenv("ARIE_DELIVERY_IDEMPOTENCY_BUCKET", "delivery-bucket")
    monkeypatch.setattr(storage, "Client", Client)

    claim = idempotency.claim_report_delivery("delivery-key")

    assert claim.acquired is False
    assert claim.state == expected_state
