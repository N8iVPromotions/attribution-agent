"""
attribution_engine.py — closed-loop revenue attribution.

Connects paid-media touchpoints (Meta / Google / TikTok / LinkedIn) to
closed/won revenue captured in a CRM (HubSpot) and/or a payment processor
(Stripe). Pure Python + pandas so it is unit-testable without Databricks or
live API credentials.

Pipeline:
  1. Build `Conversion`s from closed/won CRM deals and settled Stripe payments.
  2. Reconcile the two revenue sources so a deal paid through Stripe is not
     double-counted against its HubSpot deal amount, while Stripe revenue
     inherits the CRM's captured UTM identity for matching.
  3. For each conversion, build a journey of ad touchpoints by matching the
     conversion's captured identity (utm_source/utm_campaign → platform) to
     normalized ad-spend rows inside a lookback window ending at the close date.
  4. Allocate conversion revenue across the journey using the chosen
     attribution model (reuses `attribution_models.allocate_credit`).
  5. Roll up attributed revenue by platform and join spend to compute ROAS,
     CAC, and CPA — the closed-loop scorecard.

Data-granularity note: normalized ad spend is campaign-day aggregate, and a CRM
deal typically captures a single lead-source UTM. The honest consequence is that
most journeys are single-touch on the captured channel; multi-touch models only
diverge when a conversion carries more than one matchable touchpoint. Revenue
that cannot be matched to any paid touchpoint is reported under the
`unattributed` platform rather than silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re

import pandas as pd

from attribution_models import Touchpoint, allocate_credit

# HubSpot's default "Closed Won" stage internal id, plus common custom variants.
DEFAULT_CLOSED_WON_STAGES: tuple[str, ...] = ("closedwon", "closed_won", "won")

UNATTRIBUTED = "unattributed"

# Maps a captured utm_source value onto an ad platform (source_platform in the
# normalized ad-spend schema). Keys are lowercased.
PLATFORM_BY_UTM_SOURCE: dict[str, str] = {
    "meta": "meta",
    "facebook": "meta",
    "fb": "meta",
    "instagram": "meta",
    "ig": "meta",
    "google": "google",
    "adwords": "google",
    "google_ads": "google",
    "googleads": "google",
    "tiktok": "tiktok",
    "tik_tok": "tiktok",
    "tt": "tiktok",
    "linkedin": "linkedin",
    "li": "linkedin",
}


@dataclass(frozen=True)
class Conversion:
    """A single closed/won revenue event with its captured identity."""

    conversion_id: str
    client_id: str
    occurred_at: datetime
    revenue: float
    email: str = ""
    utm_source: str = ""
    utm_campaign: str = ""
    utm_medium: str = ""
    deal_id: str = ""
    revenue_source: str = ""  # "stripe" | "hubspot"


def _norm(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return "" if text in ("nan", "none", "null") else text


def _stage_key(value: object) -> str:
    """Canonicalize a CRM stage for exact, false-positive-safe comparison."""
    return re.sub(r"[^a-z0-9]+", "", _norm(value))


def _platform_for(utm_source: str, utm_medium: str = "") -> str:
    """Resolve a platform from utm_source (falling back to utm_medium hints)."""
    src = _norm(utm_source)
    if src in PLATFORM_BY_UTM_SOURCE:
        return PLATFORM_BY_UTM_SOURCE[src]
    med = _norm(utm_medium)
    for token, platform in PLATFORM_BY_UTM_SOURCE.items():
        if token and token in src:
            return platform
        if token and token in med:
            return platform
    return ""


def _as_datetime(value: object) -> datetime | None:
    ts = pd.to_datetime(value, errors="coerce")
    if ts is None or pd.isna(ts):
        return None
    return ts.to_pydatetime()


# ── Conversion builders ──────────────────────────────────────────────────────


def conversions_from_hubspot(
    df: pd.DataFrame,
    client_id: str,
    closed_won_stages: tuple[str, ...] = DEFAULT_CLOSED_WON_STAGES,
) -> list[Conversion]:
    """Build conversions from HubSpot deals whose stage is closed/won."""
    if df is None or df.empty:
        return []
    stage_tokens = {_stage_key(s) for s in closed_won_stages if _stage_key(s)}
    conversions: list[Conversion] = []
    for _, row in df.iterrows():
        stage = _stage_key(row.get("deal_stage"))
        if not stage or stage not in stage_tokens:
            continue
        revenue = float(row.get("amount") or 0.0)
        if revenue <= 0:
            continue
        occurred_at = _as_datetime(row.get("close_date")) or _as_datetime(
            row.get("create_date")
        )
        if occurred_at is None:
            continue
        deal_id = str(row.get("deal_id") or "")
        conversions.append(
            Conversion(
                conversion_id=f"hubspot:{deal_id}",
                client_id=client_id,
                occurred_at=occurred_at,
                revenue=revenue,
                email=_norm(row.get("contact_email")),
                utm_source=_norm(row.get("utm_source")),
                utm_campaign=_norm(row.get("utm_campaign")),
                utm_medium=_norm(row.get("utm_medium")),
                deal_id=deal_id,
                revenue_source="hubspot",
            )
        )
    return conversions


def conversions_from_stripe(df: pd.DataFrame, client_id: str) -> list[Conversion]:
    """Build conversions from settled Stripe payments (net of refunds)."""
    if df is None or df.empty:
        return []
    conversions: list[Conversion] = []
    for _, row in df.iterrows():
        status = _norm(row.get("status"))
        if status and status not in ("succeeded", "paid", "complete", "completed"):
            continue
        gross = float(row.get("amount_paid") or 0.0)
        refund = float(row.get("refund_amount") or 0.0)
        revenue = gross - refund
        if revenue <= 0:
            continue
        occurred_at = _as_datetime(row.get("created_at"))
        if occurred_at is None:
            continue
        payment_id = str(row.get("payment_id") or "")
        conversions.append(
            Conversion(
                conversion_id=f"stripe:{payment_id}",
                client_id=client_id,
                occurred_at=occurred_at,
                revenue=revenue,
                email=_norm(row.get("customer_email")),
                deal_id=str(row.get("deal_id") or ""),
                revenue_source="stripe",
            )
        )
    return conversions


def reconcile_conversions(
    hubspot_convs: list[Conversion],
    stripe_convs: list[Conversion],
    *,
    prefer: str = "stripe",
) -> list[Conversion]:
    """
    Merge CRM and Stripe conversions into one deduplicated set.

    A Stripe payment that links to a HubSpot deal (by deal_id, else by email) is
    treated as the *same* closed/won event: we keep one conversion, take its
    revenue from the preferred source (Stripe = realized cash, by default), and
    inherit the CRM's captured UTM identity so the payment is attributable.

    Unmatched HubSpot deals (e.g. contract/invoice revenue never run through
    Stripe) and unmatched Stripe payments (e.g. checkout with no CRM record) are
    both retained so revenue totals stay complete.
    """
    by_deal: dict[str, Conversion] = {}
    by_email: dict[str, Conversion] = {}
    for hc in hubspot_convs:
        if hc.deal_id:
            by_deal[hc.deal_id] = hc
        if hc.email:
            by_email.setdefault(hc.email, hc)

    reconciled: list[Conversion] = []
    consumed_hubspot: set[str] = set()

    for sc in stripe_convs:
        match = None
        if sc.deal_id and sc.deal_id in by_deal:
            match = by_deal[sc.deal_id]
        elif sc.email and sc.email in by_email:
            match = by_email[sc.email]

        if match is None:
            reconciled.append(sc)
            continue

        consumed_hubspot.add(match.conversion_id)
        revenue = sc.revenue if prefer == "stripe" else match.revenue
        reconciled.append(
            Conversion(
                conversion_id=match.conversion_id,
                client_id=match.client_id,
                occurred_at=match.occurred_at or sc.occurred_at,
                revenue=revenue,
                email=match.email or sc.email,
                utm_source=match.utm_source,
                utm_campaign=match.utm_campaign,
                utm_medium=match.utm_medium,
                deal_id=match.deal_id or sc.deal_id,
                revenue_source="stripe+hubspot" if prefer == "stripe" else "hubspot",
            )
        )

    for hc in hubspot_convs:
        if hc.conversion_id not in consumed_hubspot:
            reconciled.append(hc)

    return reconciled


# ── Journey construction + credit allocation ─────────────────────────────────


def build_journey(
    conversion: Conversion,
    ads: pd.DataFrame,
    *,
    lookback_days: int = 90,
) -> list[Touchpoint]:
    """
    Resolve the ad touchpoints that plausibly influenced a conversion.

    Matching rule: an ad-spend row qualifies when its platform matches the
    conversion's captured utm_source AND (the conversion carries no campaign, or
    the campaign matches the ad's utm_campaign/campaign_name). Qualifying rows
    are collapsed to one touchpoint per (platform, campaign), timestamped at the
    latest in-window spend date for that campaign.
    """
    if ads is None or ads.empty:
        return []
    platform = _platform_for(conversion.utm_source, conversion.utm_medium)
    if not platform:
        return []

    window_start = conversion.occurred_at - timedelta(days=lookback_days)
    conv_campaign = _norm(conversion.utm_campaign)

    frame = ads.copy()
    frame = frame[_norm_series(frame.get("client_id")) == _norm(conversion.client_id)]
    frame = frame[_norm_series(frame.get("source_platform")) == platform]
    dates = pd.to_datetime(frame.get("date"), errors="coerce")
    frame = frame[(dates >= window_start) & (dates <= conversion.occurred_at)]
    if frame.empty:
        return []

    frame = frame.assign(_date=pd.to_datetime(frame["date"], errors="coerce"))
    frame["_campaign"] = frame.apply(
        lambda r: _norm(r.get("utm_campaign")) or _norm(r.get("campaign_name")), axis=1
    )
    if conv_campaign:
        # A stated campaign that does not match is a data-quality failure, not
        # permission to spread revenue over every campaign on the platform.
        # Keep the conversion in the explicit unattributed bucket instead.
        matched = frame[frame["_campaign"] == conv_campaign]
    else:
        # Platform-only attribution is allowed only when the CRM supplied no
        # campaign identity at all.
        matched = frame
    if matched.empty:
        return []

    touchpoints: list[Touchpoint] = []
    for campaign, group in matched.groupby("_campaign"):
        rep_date = group["_date"].max().to_pydatetime()
        touchpoints.append(
            Touchpoint(
                touchpoint_id=f"{platform}:{campaign}",
                occurred_at=rep_date,
                channel=platform,
                campaign=campaign,
                source_platform=platform,
            )
        )
    return touchpoints


def _norm_series(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series([], dtype="object")
    return series.fillna("").astype(str).str.strip().str.lower()


@dataclass
class AttributionResult:
    rows: pd.DataFrame  # per-touch attributed revenue
    channel_performance: pd.DataFrame  # platform scorecard with ROAS/CAC/CPA
    total_revenue: float
    attributed_revenue: float
    unattributed_revenue: float


ATTRIBUTED_ROW_COLUMNS = [
    "client_id",
    "conversion_id",
    "source_platform",
    "campaign",
    "attribution_model",
    "credit",
    "attributed_revenue",
    "revenue_source",
    "occurred_at",
]


def attribute_conversions(
    conversions: list[Conversion],
    ads: pd.DataFrame,
    model: str,
    *,
    lookback_days: int = 90,
) -> pd.DataFrame:
    """Allocate each conversion's revenue across its journey touchpoints."""
    out_rows: list[dict] = []
    for conv in conversions:
        journey = build_journey(conv, ads, lookback_days=lookback_days)
        if not journey:
            out_rows.append(
                {
                    "client_id": conv.client_id,
                    "conversion_id": conv.conversion_id,
                    "source_platform": UNATTRIBUTED,
                    "campaign": "",
                    "attribution_model": model,
                    "credit": 1.0,
                    "attributed_revenue": conv.revenue,
                    "revenue_source": conv.revenue_source,
                    "occurred_at": conv.occurred_at,
                }
            )
            continue
        for attributed in allocate_credit(journey, model):
            tp = attributed.touchpoint
            out_rows.append(
                {
                    "client_id": conv.client_id,
                    "conversion_id": conv.conversion_id,
                    "source_platform": tp.source_platform or tp.channel,
                    "campaign": tp.campaign,
                    "attribution_model": model,
                    "credit": attributed.credit,
                    "attributed_revenue": conv.revenue * attributed.credit,
                    "revenue_source": conv.revenue_source,
                    "occurred_at": conv.occurred_at,
                }
            )
    if not out_rows:
        return pd.DataFrame(columns=ATTRIBUTED_ROW_COLUMNS)
    return pd.DataFrame(out_rows)[ATTRIBUTED_ROW_COLUMNS]


CHANNEL_PERFORMANCE_COLUMNS = [
    "client_id",
    "source_platform",
    "attribution_model",
    "spend",
    "attributed_revenue",
    "attributed_conversions",
    "roas",
    "cac",
    "cpa",
]


def channel_performance(
    attributed_rows: pd.DataFrame,
    ads: pd.DataFrame,
    model: str,
    client_id: str,
) -> pd.DataFrame:
    """
    Platform-level closed-loop scorecard: spend vs. attributed revenue with
    ROAS, CAC, and CPA. Includes an `unattributed` row for revenue that matched
    no paid touchpoint (spend = 0).
    """
    # Spend by platform.
    if ads is not None and not ads.empty:
        spend_frame = ads.copy()
        spend_frame["source_platform"] = _norm_series(
            spend_frame.get("source_platform")
        )
        spend_by = spend_frame.groupby("source_platform")["spend"].sum().to_dict()
    else:
        spend_by = {}

    # Attributed revenue + fractional conversions by platform.
    if attributed_rows is not None and not attributed_rows.empty:
        agg = attributed_rows.groupby("source_platform").agg(
            attributed_revenue=("attributed_revenue", "sum"),
            attributed_conversions=("credit", "sum"),
        )
        attr_by = agg.to_dict("index")
    else:
        attr_by = {}

    platforms = sorted(set(spend_by) | set(attr_by))
    out_rows: list[dict] = []
    for platform in platforms:
        spend = float(spend_by.get(platform, 0.0))
        stats = attr_by.get(platform, {})
        revenue = float(stats.get("attributed_revenue", 0.0))
        conversions = float(stats.get("attributed_conversions", 0.0))
        out_rows.append(
            {
                "client_id": client_id,
                "source_platform": platform,
                "attribution_model": model,
                "spend": round(spend, 2),
                "attributed_revenue": round(revenue, 2),
                "attributed_conversions": round(conversions, 4),
                "roas": round(revenue / spend, 4) if spend > 0 else None,
                "cac": round(spend / conversions, 2) if conversions > 0 else None,
                "cpa": round(spend / conversions, 2) if conversions > 0 else None,
            }
        )
    if not out_rows:
        return pd.DataFrame(columns=CHANNEL_PERFORMANCE_COLUMNS)
    return pd.DataFrame(out_rows)[CHANNEL_PERFORMANCE_COLUMNS]


def run_attribution(
    *,
    client_id: str,
    ads: pd.DataFrame,
    model: str,
    hubspot_df: pd.DataFrame | None = None,
    stripe_df: pd.DataFrame | None = None,
    lookback_days: int = 90,
    closed_won_stages: tuple[str, ...] = DEFAULT_CLOSED_WON_STAGES,
    prefer_revenue: str = "stripe",
) -> AttributionResult:
    """End-to-end closed-loop attribution for one client."""
    hubspot_convs = conversions_from_hubspot(hubspot_df, client_id, closed_won_stages)
    stripe_convs = conversions_from_stripe(stripe_df, client_id)
    conversions = reconcile_conversions(
        hubspot_convs, stripe_convs, prefer=prefer_revenue
    )

    rows = attribute_conversions(conversions, ads, model, lookback_days=lookback_days)
    scorecard = channel_performance(rows, ads, model, client_id)

    total_revenue = float(sum(c.revenue for c in conversions))
    if rows.empty:
        attributed = 0.0
    else:
        attributed = float(
            rows.loc[
                rows["source_platform"] != UNATTRIBUTED, "attributed_revenue"
            ].sum()
        )
    return AttributionResult(
        rows=rows,
        channel_performance=scorecard,
        total_revenue=round(total_revenue, 2),
        attributed_revenue=round(attributed, 2),
        unattributed_revenue=round(total_revenue - attributed, 2),
    )
