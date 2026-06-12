"""tests/test_pii_masker.py"""
import pandas as pd
import pytest
from utils.pii_masker import PIIMasker


@pytest.fixture
def masker():
    return PIIMasker()


# ── Email ─────────────────────────────────────────────────────────────────────

def test_email_in_plain_text(masker):
    text, report = masker.mask("Contact us at hello@example.com for support.")
    assert "hello@example.com" not in text
    assert "[EMAIL_1]" in text
    assert report.emails_masked == 1


def test_same_email_gets_same_placeholder(masker):
    text, report = masker.mask("From: a@b.com, also a@b.com again.")
    assert text.count("[EMAIL_1]") == 2
    assert report.emails_masked == 1


def test_two_different_emails_get_different_placeholders(masker):
    text, _ = masker.mask("a@b.com and c@d.com")
    assert "[EMAIL_1]" in text
    assert "[EMAIL_2]" in text


def test_email_in_dict(masker):
    data = {"name": "Alice", "email": "alice@test.org", "score": 95}
    masked, report = masker.mask_dict(data)
    assert masked["email"] == "[EMAIL_1]"
    assert masked["name"] == "Alice"
    assert masked["score"] == 95
    assert report.emails_masked == 1


def test_email_in_nested_dict(masker):
    data = {"contact": {"email": "x@y.com", "phone": "555-867-5309"}}
    masked, report = masker.mask_dict(data)
    assert "x@y.com" not in str(masked)
    assert report.emails_masked == 1


def test_email_in_dataframe(masker):
    df = pd.DataFrame({"contact_email": ["a@b.com", "c@d.com"], "amount": [100, 200]})
    masked_df, report = masker.mask_dataframe(df, ["contact_email"])
    assert "a@b.com" not in masked_df["contact_email"].values
    assert "c@d.com" not in masked_df["contact_email"].values
    assert masked_df["amount"].tolist() == [100, 200]
    assert report.emails_masked == 2


# ── Phone ─────────────────────────────────────────────────────────────────────

def test_us_phone_dashes(masker):
    text, report = masker.mask("Call 212-555-1234 now.")
    assert "212-555-1234" not in text
    assert "[PHONE_1]" in text
    assert report.phones_masked == 1


def test_us_phone_dots(masker):
    text, report = masker.mask("Reach me at 800.555.0199.")
    assert "800.555.0199" not in text
    assert report.phones_masked == 1


def test_us_phone_parentheses(masker):
    text, _ = masker.mask("(415) 555-9876")
    assert "555-9876" not in text


# ── SSN ───────────────────────────────────────────────────────────────────────

def test_ssn_with_dashes(masker):
    text, report = masker.mask("SSN: 123-45-6789")
    assert "123-45-6789" not in text
    assert "[SSN_1]" in text
    assert report.ssns_masked == 1


def test_ssn_with_spaces(masker):
    text, report = masker.mask("SSN 987 65 4321")
    assert "987 65 4321" not in text
    assert report.ssns_masked == 1


# ── Credit Card ───────────────────────────────────────────────────────────────

def test_valid_luhn_card(masker):
    # Visa test card number — passes Luhn
    text, report = masker.mask("Card: 4111111111111111")
    assert "4111111111111111" not in text
    assert report.credit_cards_masked == 1


def test_invalid_luhn_not_masked(masker):
    # Fails Luhn — should not be masked
    text, report = masker.mask("Number: 4111111111111112")
    assert "4111111111111112" in text
    assert report.credit_cards_masked == 0


# ── No false positives ────────────────────────────────────────────────────────

def test_dollar_amount_not_masked(masker):
    text, report = masker.mask("Total pipeline: $16,000.00")
    assert "$16,000.00" in text
    assert report.total == 0


def test_campaign_id_not_masked(masker):
    text, report = masker.mask("Campaign ID: 155554968273585")
    assert "155554968273585" in text
    assert report.total == 0


def test_roi_metric_not_masked(masker):
    text, report = masker.mask("ROI: 3.45x | Spend: $2,000")
    assert "3.45x" in text
    assert report.total == 0


def test_date_not_masked(masker):
    text, report = masker.mask("Period: 2026-05-01 to 2026-06-01")
    assert "2026-05-01" in text
    assert report.total == 0


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_masking_already_masked_text_is_noop(masker):
    original = "Contact: user@example.com, phone 555-867-5309"
    masked, r1 = masker.mask(original)
    re_masked, r2 = masker.mask(masked)
    assert masked == re_masked
    assert r2.total == 0


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_string(masker):
    result, report = masker.mask("")
    assert result == ""
    assert report.total == 0


def test_non_string_passthrough(masker):
    result, report = masker.mask(None)
    assert result is None
    assert report.total == 0


def test_dataframe_missing_column_is_ignored(masker):
    df = pd.DataFrame({"amount": [100]})
    masked_df, report = masker.mask_dataframe(df, ["contact_email"])
    assert list(masked_df.columns) == ["amount"]
    assert report.total == 0
