"""
agents/ingest/validator.py
---------------------------
Validates raw DataFrames before writing to Databricks.
Catches data quality issues BEFORE they poison the attribution model.

Two outputs:
  1. A validated (possibly filtered) DataFrame ready to write
  2. A ValidationReport with any issues flagged for alerting
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
import pyarrow as pa
from loguru import logger


# ─── REPORT ───────────────────────────────────────────────────────────────────


@dataclass
class ValidationReport:
    source: str
    client_id: str
    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)
        logger.warning(f"[Validator:{self.source}] ⚠ {msg}")

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False
        logger.error(f"[Validator:{self.source}] ✗ {msg}")

    def summary(self) -> str:
        status = "✓ PASSED" if self.passed else "✗ FAILED"
        lines = [f"Validation {status} | {self.source} | {self.client_id}"]
        for w in self.warnings:
            lines.append(f"  ⚠  {w}")
        for e in self.errors:
            lines.append(f"  ✗  {e}")
        return "\n".join(lines)


# ─── META VALIDATOR ───────────────────────────────────────────────────────────


class MetaValidator:
    REQUIRED_COLUMNS = {
        "ad_account_id",
        "campaign_id",
        "date",
        "spend",
        "impressions",
        "clicks",
    }

    def __init__(
        self,
        client_id: str,
        spend_drop_pct_alert: float = 0.30,
        zero_spend_days_allowed: int = 1,
    ) -> None:
        self.client_id = client_id
        self.spend_drop_pct_alert = spend_drop_pct_alert
        self.zero_spend_days_allowed = zero_spend_days_allowed

    def validate(self, df: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]:
        report = ValidationReport(source="meta", client_id=self.client_id)

        # ── 1. Empty DataFrame ─────────────────────────────────────────────────
        if df.empty:
            report.add_error("DataFrame is empty — no Meta data returned")
            return df, report

        # ── 2. Required columns ────────────────────────────────────────────────
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            report.add_error(f"Missing required columns: {missing}")
            return df, report

        # ── 3. Zero-spend days ────────────────────────────────────────────────
        daily_spend = df.groupby("date")["spend"].sum()
        zero_days = (daily_spend == 0).sum()
        if zero_days > self.zero_spend_days_allowed:
            report.add_error(
                f"{zero_days} days with $0 spend — possible tracking gap "
                f"(threshold: {self.zero_spend_days_allowed})"
            )

        # ── 4. Spend anomaly (day-over-day drop) ───────────────────────────────
        if len(daily_spend) >= 2:
            pct_changes = daily_spend.pct_change().dropna()
            big_drops = pct_changes[pct_changes < -self.spend_drop_pct_alert]
            for date_val, pct in big_drops.items():
                date_label = pd.to_datetime(date_val).date() if date_val else date_val
                report.add_warning(
                    f"Spend dropped {abs(pct):.0%} on {date_label} "
                    f"— verify campaign status"
                )

        # ── 5. Negative values ────────────────────────────────────────────────
        for col in ["spend", "impressions", "clicks"]:
            if (df[col] < 0).any():
                report.add_error(f"Negative values found in column '{col}'")

        # ── 6. Date range sanity ──────────────────────────────────────────────
        if "date" in df.columns:
            max_date = pd.to_datetime(df["date"], errors="coerce").max()
            today = pd.Timestamp.today().normalize()
            if pd.notna(max_date) and max_date >= today:
                report.add_warning(
                    f"Data includes today ({max_date.date()}) — "
                    "today's spend is not yet finalized"
                )

        # ── 7. Null campaign IDs ──────────────────────────────────────────────
        null_campaign_pct = df["campaign_id"].isna().mean()
        if null_campaign_pct > 0.05:
            report.add_warning(f"{null_campaign_pct:.0%} of rows have null campaign_id")

        logger.info(report.summary())
        return df, report


# ─── HUBSPOT VALIDATOR ────────────────────────────────────────────────────────


class HubSpotValidator:
    REQUIRED_COLUMNS = {
        "deal_id",
        "deal_stage",
        "amount",
        "deal_currency_code",
        "create_date",
        "hs_source",
        "hs_source_detail_1",
        "hs_source_detail_2",
    }

    def __init__(self, client_id: str, expected_currency: str = "USD") -> None:
        self.client_id = client_id
        self.expected_currency = expected_currency.strip().upper()

    def validate(self, df: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]:
        report = ValidationReport(source="hubspot", client_id=self.client_id)

        # ── 1. Empty DataFrame ─────────────────────────────────────────────────
        if df.empty:
            report.add_warning("DataFrame is empty — no HubSpot deals returned")
            return df, report

        # ── 2. Required columns ────────────────────────────────────────────────
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            report.add_error(f"Missing required columns: {missing}")
            return df, report

        # ── 3. Missing attribution data ────────────────────────────────────────
        unattributed = df["hs_source"].isna() | (df["hs_source"] == "")
        unattributed_pct = unattributed.mean()
        if unattributed_pct > 0.20:
            report.add_warning(
                f"{unattributed_pct:.0%} of deals have no source attribution — "
                "check UTM tagging and HubSpot tracking code"
            )

        # ── 4. Duplicate deal IDs ──────────────────────────────────────────────
        dupes = df["deal_id"].duplicated().sum()
        if dupes > 0:
            report.add_warning(f"{dupes} duplicate deal_id rows found — deduplicating")
            df = df.drop_duplicates(subset=["deal_id"], keep="last")

        # ── 5. Negative deal amounts ──────────────────────────────────────────
        if df["amount"].notna().any():
            negative_amounts = (df["amount"].dropna() < 0).sum()
            if negative_amounts > 0:
                report.add_error(f"{negative_amounts} deals with negative amounts")

        monetary_rows = df["amount"].notna() & df["amount"].ne(0)
        currencies = (
            df.loc[monetary_rows, "deal_currency_code"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )
        if currencies.eq("").any():
            report.add_error(
                "HubSpot deal currency is missing; USD-only reporting cannot be "
                "verified"
            )
        unexpected = sorted(
            set(currencies[currencies.ne("") & currencies.ne(self.expected_currency)])
        )
        if unexpected:
            report.add_error(
                "HubSpot contains non-USD monetary deals: " + ", ".join(unexpected)
            )

        # ── 6. Deal-level paid-source identity coverage ───────────────────────
        source_keys = df["hs_source"].fillna("").astype(str).str.strip().str.upper()
        paid_rows = source_keys.isin({"PAID_SEARCH", "PAID_SOCIAL"})
        if paid_rows.any():
            detail_1 = (
                df.loc[paid_rows, "hs_source_detail_1"]
                .fillna("")
                .astype(str)
                .str.strip()
            )
            paid_sources = source_keys.loc[paid_rows]
            missing_platform = paid_sources.eq("PAID_SOCIAL") & detail_1.eq("")
            if missing_platform.any():
                report.add_warning(
                    f"{missing_platform.mean():.0%} of paid-source deals lack the "
                    "HubSpot network detail required for platform matching"
                )

            detail_2 = (
                df.loc[paid_rows, "hs_source_detail_2"]
                .fillna("")
                .astype(str)
                .str.strip()
            )
            has_campaign = (paid_sources.eq("PAID_SEARCH") & detail_1.ne("")) | (
                paid_sources.eq("PAID_SOCIAL") & detail_2.ne("")
            )
            campaign_coverage = has_campaign.mean()
            if campaign_coverage < 0.50:
                report.add_warning(
                    f"Only {campaign_coverage:.0%} of paid-source deals have a "
                    "HubSpot deal-level campaign identity — remaining matches are "
                    "limited to the recorded platform"
                )

        logger.info(report.summary())
        return df, report


# ─── CONVENIENCE FUNCTIONS (called by Prefect flow) ───────────────────────────


def validate_meta(
    df: pd.DataFrame,
    client_id: str,
    spend_drop_pct_alert: float = 0.30,
    zero_spend_days_allowed: int = 1,
) -> tuple[pd.DataFrame, ValidationReport]:
    return MetaValidator(
        client_id=client_id,
        spend_drop_pct_alert=spend_drop_pct_alert,
        zero_spend_days_allowed=zero_spend_days_allowed,
    ).validate(df)


def validate_hubspot(
    df: pd.DataFrame,
    client_id: str,
    expected_currency: str = "USD",
) -> tuple[pd.DataFrame, ValidationReport]:
    return HubSpotValidator(
        client_id=client_id, expected_currency=expected_currency
    ).validate(df)


# ─── STRIPE VALIDATOR ─────────────────────────────────────────────────────────


class StripeValidator:
    REQUIRED_COLUMNS = {
        "payment_id",
        "hubspot_deal_id",
        "amount_paid",
        "currency",
        "livemode",
        "status",
    }

    def __init__(
        self,
        client_id: str,
        expected_currency: str = "USD",
        require_live_mode: bool = False,
    ) -> None:
        self.client_id = client_id
        self.expected_currency = expected_currency.strip().upper()
        self.require_live_mode = require_live_mode

    def validate(self, df: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]:
        report = ValidationReport(source="stripe", client_id=self.client_id)

        # ── 1. Empty DataFrame ─────────────────────────────────────────────────
        if df.empty:
            report.add_error("DataFrame is empty — no Stripe payments returned")
            return df, report

        # ── 2. Required columns ────────────────────────────────────────────────
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            report.add_error(f"Missing required columns: {missing}")
            return df, report

        # ── 3. Negative amounts ────────────────────────────────────────────────
        negative = (df["amount_paid"] < 0).sum()
        if negative > 0:
            report.add_error(f"{negative} payments with negative amount_paid")

        currencies = df["currency"].fillna("").astype(str).str.strip().str.upper()
        if currencies.eq("").any():
            report.add_error(
                "Stripe payment currency is missing; USD-only reporting cannot be "
                "verified"
            )
        unexpected = sorted(
            set(currencies[currencies.ne("") & currencies.ne(self.expected_currency)])
        )
        if unexpected:
            report.add_error(
                "Stripe contains non-USD payments: " + ", ".join(unexpected)
            )
        if (
            self.require_live_mode
            and not df["livemode"].fillna(False).astype(bool).all()
        ):
            report.add_error(
                "Stripe test-mode payments cannot be used for live report delivery"
            )

        # ── 4. Duplicate payment IDs ───────────────────────────────────────────
        dupes = df["payment_id"].duplicated().sum()
        if dupes > 0:
            report.add_warning(f"{dupes} duplicate payment_id rows — deduplicating")
            df = df.drop_duplicates(subset=["payment_id"], keep="last")

        # ── 5. Exact HubSpot deal ID coverage ──────────────────────────────────
        deal_ids = df["hubspot_deal_id"].fillna("").astype(str).str.strip()
        missing_deal_id_pct = deal_ids.eq("").mean()
        if missing_deal_id_pct == 1.0:
            report.add_error(
                "All payments missing hubspot_deal_id — exact HubSpot revenue join "
                "is impossible"
            )
        elif missing_deal_id_pct > 0:
            report.add_warning(
                f"{missing_deal_id_pct:.0%} of payments missing hubspot_deal_id — "
                "those payments will remain unattributed"
            )

        # Email is diagnostic only; production attribution never substitutes it
        # for the explicit deal identifier.
        if "customer_email" in df.columns:
            emails = df["customer_email"].fillna("").astype(str).str.strip()
            missing_email_pct = emails.eq("").mean()
            if missing_email_pct > 0:
                report.add_warning(
                    f"{missing_email_pct:.0%} of payments missing optional "
                    "customer_email"
                )

        # ── 6. Refund rate anomaly ─────────────────────────────────────────────
        if "refund_amount" in df.columns:
            total_paid = df["amount_paid"].sum()
            total_refunded = df["refund_amount"].sum()
            if total_paid > 0:
                refund_rate = total_refunded / total_paid
                if refund_rate > 0.20:
                    report.add_warning(
                        f"Refund rate is {refund_rate:.0%} — "
                        "investigate product or billing issues"
                    )

        logger.info(report.summary())
        return df, report


def validate_stripe(
    df: pd.DataFrame,
    client_id: str,
    expected_currency: str = "USD",
    require_live_mode: bool = False,
) -> tuple[pd.DataFrame, ValidationReport]:
    return StripeValidator(
        client_id=client_id,
        expected_currency=expected_currency,
        require_live_mode=require_live_mode,
    ).validate(df)


def validate_record_batch(
    batch: pa.RecordBatch,
    validator: Callable[[pd.DataFrame], tuple[pd.DataFrame, ValidationReport]],
) -> tuple[pa.RecordBatch, ValidationReport]:
    """Validate one bounded batch and return its filtered Arrow representation."""
    validated, report = validator(batch.to_pandas())
    return pa.RecordBatch.from_pandas(validated, preserve_index=False), report
