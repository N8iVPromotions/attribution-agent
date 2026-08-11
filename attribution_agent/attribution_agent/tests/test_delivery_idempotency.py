from __future__ import annotations

from utils import idempotency


def test_report_delivery_key_is_deterministic_and_model_scoped():
    assert idempotency.report_delivery_key("client-a", "2026-07", "linear") == (
        "report:client-a:2026-07:linear"
    )
    assert idempotency.report_delivery_key("client-a", "2026-07", "last_touch") != (
        idempotency.report_delivery_key("client-a", "2026-07", "linear")
    )


def test_local_delivery_claim_prevents_duplicates_and_releases_failures(monkeypatch):
    monkeypatch.delenv("ARIE_DELIVERY_IDEMPOTENCY_BUCKET", raising=False)
    idempotency._LOCAL_DELIVERY_KEYS.clear()

    first = idempotency.claim_report_delivery("report:c:2026-07:linear")
    duplicate = idempotency.claim_report_delivery("report:c:2026-07:linear")

    assert first.acquired is True
    assert duplicate.acquired is False

    first.release()
    retry = idempotency.claim_report_delivery("report:c:2026-07:linear")
    assert retry.acquired is True
