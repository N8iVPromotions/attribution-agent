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

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
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
    REQUIRED_COLUMNS = {"ad_account_id", "campaign_id", "date", "spend", "impressions", "clicks"}

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
            report.add_warning(
                f"{null_campaign_pct:.0%} of rows have null campaign_id"
            )

        logger.info(report.summary())
        return df, report


# ─── HUBSPOT VALIDATOR ────────────────────────────────────────────────────────

class HubSpotValidator:
    REQUIRED_COLUMNS = {"deal_id", "deal_stage", "amount", "create_date"}

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id

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

        # ── 6. UTM coverage check ─────────────────────────────────────────────
        if "utm_campaign" in df.columns:
            utm_coverage = df["utm_campaign"].notna().mean()
            if utm_coverage < 0.50:
                report.add_warning(
                    f"Only {utm_coverage:.0%} of deals have utm_campaign set — "
                    "UTM tagging on paid campaigns may be incomplete"
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
) -> tuple[pd.DataFrame, ValidationReport]:
    return HubSpotValidator(client_id=client_id).validate(df)


# ─── STRIPE VALIDATOR ─────────────────────────────────────────────────────────

class StripeValidator:
    REQUIRED_COLUMNS = {"payment_id", "customer_email", "amount_paid", "status"}

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id

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

        # ── 4. Duplicate payment IDs ───────────────────────────────────────────
        dupes = df["payment_id"].duplicated().sum()
        if dupes > 0:
            report.add_warning(f"{dupes} duplicate payment_id rows — deduplicating")
            df = df.drop_duplicates(subset=["payment_id"], keep="last")

        # ── 5. Missing customer email (required for HubSpot join) ──────────────
        missing_email = df["customer_email"].isna() | (df["customer_email"] == "")
        missing_email_pct = missing_email.mean()
        if missing_email_pct == 1.0:
            report.add_error(
                "All payments missing customer_email — HubSpot revenue join impossible"
            )
        elif missing_email_pct > 0.10:
            report.add_warning(
                f"{missing_email_pct:.0%} of payments missing customer_email — "
                "HubSpot join coverage will be reduced"
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
) -> tuple[pd.DataFrame, ValidationReport]:
    return StripeValidator(client_id=client_id).validate(df)
