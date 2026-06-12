"""
utils/pii_masker.py
--------------------
Stateless, deterministic PII masking utility.

The same PII token always maps to the same placeholder within a single
mask() call, preserving referential integrity for downstream joins
(e.g. two rows sharing the same email both become [EMAIL_1]).

No external dependencies beyond the standard library and pandas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# ── Patterns ──────────────────────────────────────────────────────────────────

_EMAIL_RE    = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)
_PHONE_RE    = re.compile(
    r"(?<!\d)"
    r"(\+?1[\s.\-]?)?"
    r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"
    r"(?!\d)"
)
_SSN_RE      = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
_CC_RE       = re.compile(r"\b(?:\d[ \-]?){13,16}\b")

# Luhn check to reduce false positives on CC pattern
def _luhn(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if len(digits) < 13:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        total += d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2)
    return total % 10 == 0


@dataclass
class MaskingReport:
    emails_masked: int = 0
    phones_masked: int = 0
    ssns_masked: int = 0
    credit_cards_masked: int = 0

    @property
    def total(self) -> int:
        return self.emails_masked + self.phones_masked + self.ssns_masked + self.credit_cards_masked


@dataclass
class _MaskingState:
    """Tracks seen values → placeholder within a single mask() call."""
    seen_emails: dict[str, str] = field(default_factory=dict)
    seen_phones: dict[str, str] = field(default_factory=dict)
    seen_ssns:   dict[str, str] = field(default_factory=dict)
    seen_cards:  dict[str, str] = field(default_factory=dict)
    report: MaskingReport = field(default_factory=MaskingReport)

    def _replace(self, registry: dict, value: str, prefix: str, counter_attr: str) -> str:
        key = value.lower().strip()
        if key not in registry:
            n = len(registry) + 1
            registry[key] = f"[{prefix}_{n}]"
            setattr(self.report, counter_attr, getattr(self.report, counter_attr) + 1)
        return registry[key]

    def sub_email(self, m: re.Match) -> str:
        return self._replace(self.seen_emails, m.group(), "EMAIL", "emails_masked")

    def sub_phone(self, m: re.Match) -> str:
        return self._replace(self.seen_phones, m.group(), "PHONE", "phones_masked")

    def sub_ssn(self, m: re.Match) -> str:
        return self._replace(self.seen_ssns, m.group(), "SSN", "ssns_masked")

    def sub_card(self, m: re.Match) -> str:
        digits_only = re.sub(r"\D", "", m.group())
        if not _luhn(digits_only):
            return m.group()
        return self._replace(self.seen_cards, m.group(), "CC", "credit_cards_masked")


class PIIMasker:
    """
    Thread-safe, stateless masker.  Each call to mask() creates its own
    internal state so parallel calls do not interfere.
    """

    def mask(self, text: str) -> tuple[str, MaskingReport]:
        """
        Mask PII in a plain string.

        Returns (masked_text, MaskingReport).
        """
        if not isinstance(text, str) or not text:
            return text, MaskingReport()

        state = _MaskingState()
        text = _EMAIL_RE.sub(state.sub_email, text)
        text = _PHONE_RE.sub(state.sub_phone, text)
        text = _SSN_RE.sub(state.sub_ssn, text)
        text = _CC_RE.sub(state.sub_card, text)
        return text, state.report

    def mask_text(self, text: str) -> str:
        """Convenience wrapper — returns only the masked string."""
        result, _ = self.mask(text)
        return result

    def mask_dict(self, data: dict) -> tuple[dict, MaskingReport]:
        """
        Recursively mask all string values in a dict.
        Keys are not masked.  Nested dicts and lists are handled.
        Returns (masked_dict, MaskingReport).
        """
        state = _MaskingState()
        masked = self._mask_value(data, state)
        return masked, state.report

    def _mask_value(self, value, state: _MaskingState):
        if isinstance(value, str):
            value = _EMAIL_RE.sub(state.sub_email, value)
            value = _PHONE_RE.sub(state.sub_phone, value)
            value = _SSN_RE.sub(state.sub_ssn, value)
            value = _CC_RE.sub(state.sub_card, value)
            return value
        if isinstance(value, dict):
            return {k: self._mask_value(v, state) for k, v in value.items()}
        if isinstance(value, list):
            return [self._mask_value(item, state) for item in value]
        return value

    def mask_dataframe(
        self,
        df: "pd.DataFrame",
        columns: list[str],
    ) -> tuple["pd.DataFrame", MaskingReport]:
        """
        Mask specified string columns in a DataFrame in-place (copy).
        Returns (masked_df, MaskingReport).
        """
        import pandas as pd  # noqa: F401 — optional import

        df = df.copy()
        state = _MaskingState()
        for col in columns:
            if col not in df.columns:
                continue
            df[col] = df[col].apply(
                lambda v: self._mask_value(v, state) if isinstance(v, str) else v
            )
        return df, state.report
