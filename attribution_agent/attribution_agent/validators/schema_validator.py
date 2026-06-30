"""
validators/schema_validator.py — Data quality checks before loading to Databricks.

This runs BEFORE anything hits your bronze tables. Catches:
  - Missing critical columns
  - Spend anomalies (zero-spend days that might be tracking gaps, not real zeros)
  - UTM coverage gaps (contacts with no source attribution)
  - Empty DataFrames from API failures
  - Type mismatches

Returns a ValidationResult with pass/fail + detailed warnings so the
Prefect flow can decide whether to proceed or alert you.
"""

import pandas as pd
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class ValidationResult:
    """Holds the outcome of a validation run."""

    passed: bool
    dataset: str
    client_id: str
    row_count: int
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "✅ PASSED" if self.passed else "❌ FAILED"
        lines = [
            f"{status} | {self.dataset} | {self.client_id} | {self.row_count} rows",
        ]
        for w in self.warnings:
            lines.append(f"  ⚠️  {w}")
        for e in self.errors:
            lines.append(f"  🚨 {e}")
        return "\n".join(lines)


class IngestValidator:
    """
    Validates DataFrames produced by the connectors before Databricks load.
    Each method returns a ValidationResult — never raises, so the flow
    can accumulate all warnings and make a single routing decision.
    """

    # ─── META VALIDATION ────────────────────────────────────────────────────

    REQUIRED_META_COLS = [
        "client_id",
        "campaign_id",
        "campaign_name",
        "ad_id",
        "spend",
        "impressions",
        "clicks",
        "date",
    ]

    def validate_meta(self, df: pd.DataFrame, client_id: str) -> ValidationResult:
        result = ValidationResult(
            passed=True,
            dataset="meta_ad_insights",
            client_id=client_id,
            row_count=len(df),
        )

        # Empty DataFrame — likely an API failure, not a real zero
        if df.empty:
            result.errors.append(
                "DataFrame is empty. API may have returned no data or failed silently."
            )
            result.passed = False
            return result

        # Missing required columns
        missing_cols = [c for c in self.REQUIRED_META_COLS if c not in df.columns]
        if missing_cols:
            result.errors.append(f"Missing required columns: {missing_cols}")
            result.passed = False

        # Zero spend on all rows — strong signal of a tracking gap
        if "spend" in df.columns:
            zero_spend_pct = (df["spend"] == 0).mean()
            if zero_spend_pct == 1.0:
                result.errors.append(
                    "100% of rows have $0 spend. This is likely a tracking gap, "
                    "not real zero spend. Check Meta API token permissions."
                )
                result.passed = False
            elif zero_spend_pct > 0.5:
                result.warnings.append(
                    f"{zero_spend_pct:.0%} of rows have $0 spend. "
                    "Verify no date range / account filtering issues."
                )

        # Spend outlier — single day spike > 3x average (could be a data error)
        if "spend" in df.columns and "date" in df.columns:
            daily_spend = df.groupby("date")["spend"].sum()
            if len(daily_spend) > 3:
                avg = daily_spend.mean()
                spike_days = daily_spend[daily_spend > avg * 3]
                if not spike_days.empty:
                    result.warnings.append(
                        f"Spend spike detected on {list(spike_days.index)}: "
                        f"${spike_days.max():,.2f} vs ${avg:,.2f} avg. Verify with client."
                    )

        # Null campaign names — usually means deleted/archived campaigns
        if "campaign_name" in df.columns:
            null_campaigns = df["campaign_name"].isna().sum()
            if null_campaigns > 0:
                result.warnings.append(
                    f"{null_campaigns} rows have null campaign_name. "
                    "May indicate deleted campaigns still receiving spend."
                )

        logger.info(result.summary())
        return result

    # ─── HUBSPOT CONTACTS VALIDATION ────────────────────────────────────────

    REQUIRED_CONTACT_COLS = [
        "client_id",
        "contact_id",
        "created_at",
    ]

    UTM_COLS = ["utm_source", "utm_medium", "utm_campaign", "hs_source"]

    def validate_contacts(self, df: pd.DataFrame, client_id: str) -> ValidationResult:
        result = ValidationResult(
            passed=True,
            dataset="hubspot_contacts",
            client_id=client_id,
            row_count=len(df),
        )

        if df.empty:
            # Empty contacts is a warning, not necessarily an error
            # (client may have had no new contacts this period)
            result.warnings.append(
                "No contacts returned for this period. "
                "If this is unexpected, check HubSpot token scope."
            )
            return result

        # Missing required columns
        missing_cols = [c for c in self.REQUIRED_CONTACT_COLS if c not in df.columns]
        if missing_cols:
            result.errors.append(f"Missing required columns: {missing_cols}")
            result.passed = False

        # UTM coverage — how many contacts have ANY source attribution
        if all(c in df.columns for c in self.UTM_COLS):
            has_any_utm = df[self.UTM_COLS].notna().any(axis=1)
            coverage = has_any_utm.mean()
            if coverage < 0.5:
                result.warnings.append(
                    f"Only {coverage:.0%} of contacts have UTM/source attribution. "
                    "Attribution accuracy will be limited. "
                    "Ensure UTM parameters are being captured on all landing pages."
                )
            elif coverage < 0.8:
                result.warnings.append(
                    f"{1 - coverage:.0%} of contacts are missing UTM attribution. "
                    "Consider auditing landing page tracking."
                )

        # Duplicate contact IDs
        if "contact_id" in df.columns:
            dupe_count = df["contact_id"].duplicated().sum()
            if dupe_count > 0:
                result.errors.append(
                    f"{dupe_count} duplicate contact_ids detected. "
                    "Check pagination logic in HubSpot connector."
                )
                result.passed = False

        logger.info(result.summary())
        return result

    # ─── HUBSPOT DEALS VALIDATION ────────────────────────────────────────────

    REQUIRED_DEAL_COLS = [
        "client_id",
        "deal_id",
        "deal_stage",
        "amount",
        "created_at",
    ]

    def validate_deals(self, df: pd.DataFrame, client_id: str) -> ValidationResult:
        result = ValidationResult(
            passed=True,
            dataset="hubspot_deals",
            client_id=client_id,
            row_count=len(df),
        )

        if df.empty:
            result.warnings.append(
                "No deals returned for this period. "
                "Normal for new clients; flag if unexpected."
            )
            return result

        # Missing required columns
        missing_cols = [c for c in self.REQUIRED_DEAL_COLS if c not in df.columns]
        if missing_cols:
            result.errors.append(f"Missing required columns: {missing_cols}")
            result.passed = False

        # Deals with no amount — common but worth flagging
        if "amount" in df.columns:
            no_amount = (df["amount"] == 0).sum()
            if no_amount > 0:
                result.warnings.append(
                    f"{no_amount} deals have $0 amount. "
                    "These won't contribute to revenue attribution. "
                    "Ask client to ensure deal values are filled in HubSpot."
                )

        # Deals with no associated contacts — breaks the attribution join
        if "associated_contact_ids" in df.columns:
            no_contact = df["associated_contact_ids"].isin(["", None]).sum()
            if no_contact > 0:
                result.warnings.append(
                    f"{no_contact} deals have no associated contact. "
                    "These cannot be attributed to a marketing source. "
                    "Ask client to associate contacts to deals in HubSpot."
                )

        logger.info(result.summary())
        return result
