// Demo dataset for the ARIE Command Center pitch mode.
//
// Five fictional pilot clients with six months of internally consistent
// channel data (spend -> attributed pipeline -> collected revenue -> ROI).
// Everything is derived from the per-channel profiles below so the story
// holds together on any screen: numbers in the overview, the client cards,
// the trend charts, and the insight reports all reconcile.

export const MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"];
export const LATEST_MONTH = MONTHS[MONTHS.length - 1];

export const CHANNELS = [
  { id: "meta", label: "Meta Ads" },
  { id: "google_ads", label: "Google Ads" },
  { id: "linkedin_ads", label: "LinkedIn Ads" },
  { id: "tiktok_ads", label: "TikTok Ads" },
];

// Deterministic wobble so charts look organic but every reload matches.
function wobble(seed, i) {
  const x = Math.sin(seed * 997 + i * 131) * 10000;
  return x - Math.floor(x); // 0..1
}

// channels: { channelId: { spend, spendGrowth, roas, roasGrowth } }
// spend is month-1 spend in dollars; growth is per-month multiplier.
const CLIENT_PROFILES = [
  {
    client_id: "summit_roofing",
    client_name: "Summit Roofing Co.",
    vertical: "Home services",
    agency_id: "n8iv_promotions",
    attribution_model: "u_shape",
    lookback_days: 60,
    client_report_email: "owner@summitroofing.example",
    contact: "Dana Whitfield",
    sources: { hubspot: true, stripe: true },
    refund_rate: 0.012,
    collect_rate: 0.64, // share of attributed pipeline collected via Stripe
    channels: {
      meta: { spend: 9800, spendGrowth: 1.04, roas: 3.1, roasGrowth: 1.02, seed: 11 },
      google_ads: { spend: 12600, spendGrowth: 1.02, roas: 4.4, roasGrowth: 1.03, seed: 12 },
    },
    story:
      "Storm-season demand spike; Google Search branded terms carry the highest close rate.",
  },
  {
    client_id: "luxe_medspa",
    client_name: "Luxe Aesthetics MedSpa",
    vertical: "Health & beauty",
    agency_id: "n8iv_promotions",
    attribution_model: "time_decay",
    lookback_days: 30,
    client_report_email: "kelly@luxemedspa.example",
    contact: "Kelly Tran",
    sources: { hubspot: true, stripe: true },
    refund_rate: 0.041,
    collect_rate: 0.82,
    channels: {
      meta: { spend: 8400, spendGrowth: 1.01, roas: 2.7, roasGrowth: 0.99, seed: 21 },
      tiktok_ads: { spend: 5200, spendGrowth: 1.09, roas: 2.1, roasGrowth: 1.08, seed: 22 },
    },
    story:
      "TikTok creator whitelisting is compounding; ROAS crossed Meta in the latest month.",
  },
  {
    client_id: "apex_fitness",
    client_name: "Apex Fitness Studios",
    vertical: "Fitness memberships",
    agency_id: "n8iv_promotions",
    attribution_model: "linear",
    lookback_days: 30,
    client_report_email: "marcus@apexfit.example",
    contact: "Marcus Bell",
    sources: { hubspot: false, stripe: true },
    refund_rate: 0.058,
    collect_rate: 0.91,
    channels: {
      meta: { spend: 7600, spendGrowth: 1.03, roas: 3.4, roasGrowth: 1.0, seed: 31 },
      google_ads: { spend: 4100, spendGrowth: 1.0, roas: 2.9, roasGrowth: 0.98, seed: 32 },
    },
    story:
      "New-year membership cohort retained well; January promo refunds now fully washed out.",
  },
  {
    client_id: "harbor_legal",
    client_name: "Harbor Legal Group",
    vertical: "B2B legal services",
    agency_id: "n8iv_promotions",
    attribution_model: "w_shape",
    lookback_days: 90,
    client_report_email: "intake@harborlegal.example",
    contact: "Priya Raman",
    sources: { hubspot: true, stripe: false },
    refund_rate: 0.0,
    collect_rate: 0.38, // long sales cycle; most pipeline not yet collected
    channels: {
      google_ads: { spend: 9200, spendGrowth: 1.01, roas: 5.2, roasGrowth: 1.01, seed: 41 },
      linkedin_ads: { spend: 6800, spendGrowth: 1.05, roas: 3.8, roasGrowth: 1.06, seed: 42 },
    },
    story:
      "LinkedIn CPL is high but average matter value is 6x other channels; pipeline-weighted ROI justifies the spend.",
  },
  {
    client_id: "greentrail_outfitters",
    client_name: "GreenTrail Outfitters",
    vertical: "Outdoor e-commerce",
    agency_id: "n8iv_promotions",
    attribution_model: "last_touch",
    lookback_days: 30,
    client_report_email: "ops@greentrail.example",
    contact: "Sam Okafor",
    sources: { hubspot: false, stripe: true },
    refund_rate: 0.067,
    collect_rate: 1.0, // pure e-commerce: pipeline == collected
    channels: {
      meta: { spend: 14200, spendGrowth: 1.02, roas: 3.9, roasGrowth: 1.01, seed: 51 },
      google_ads: { spend: 9800, spendGrowth: 1.03, roas: 4.6, roasGrowth: 1.0, seed: 52 },
      tiktok_ads: { spend: 6100, spendGrowth: 1.12, roas: 1.8, roasGrowth: 1.05, seed: 53 },
    },
    story:
      "Spring gear launch scaled Meta prospecting; TikTok still below breakeven but improving monthly.",
  },
];

function round(n) {
  return Math.round(n);
}

// Build per-client monthly channel series: [{ month, channel, spend, revenue }]
function buildSeries(profile) {
  const rows = [];
  for (const [channelId, ch] of Object.entries(profile.channels)) {
    for (let i = 0; i < MONTHS.length; i++) {
      const w = 0.92 + wobble(ch.seed, i) * 0.16; // ±8% noise
      const spend = ch.spend * Math.pow(ch.spendGrowth, i) * w;
      const roas = ch.roas * Math.pow(ch.roasGrowth, i) * (0.95 + wobble(ch.seed + 7, i) * 0.1);
      rows.push({
        month: MONTHS[i],
        channel: channelId,
        spend: round(spend),
        revenue: round(spend * roas),
      });
    }
  }
  return rows;
}

function monthTotals(series, month) {
  const rows = series.filter((r) => r.month === month);
  const spend = rows.reduce((s, r) => s + r.spend, 0);
  const revenue = rows.reduce((s, r) => s + r.revenue, 0);
  return { spend, revenue, rows };
}

function topChannel(rows) {
  let best = null;
  for (const r of rows) {
    if (!best || r.revenue > best.revenue) best = r;
  }
  return best ? best.channel : "";
}

const channelLabel = (id) => CHANNELS.find((c) => c.id === id)?.label ?? id;

// ── Narrative + findings per client (what the two-stage insight agents emit) ──

const NARRATIVES = {
  summit_roofing: (m) =>
    `Attributed pipeline reached $${m.revenue.toLocaleString()} on $${m.spend.toLocaleString()} of ad spend in ${LATEST_MONTH}, a blended ${(m.revenue / m.spend).toFixed(1)}x return under the U-shape model. Google Search remains the workhorse: branded and storm-damage terms drove the majority of closed-won roofing jobs, and its cost per closed job fell for the third straight month. Meta continues to fill the top of the funnel — over half of Google-closed deals had a Meta first touch inside the 60-day lookback, which the U-shape model now credits properly. Recommended action: shift 10% of Meta retargeting budget into Google Search while storm-season demand holds.`,
  luxe_medspa: (m) =>
    `${LATEST_MONTH} closed with $${m.revenue.toLocaleString()} in attributed revenue against $${m.spend.toLocaleString()} in spend. The headline: TikTok's creator-whitelisting program has compounded for four consecutive months and its ROAS overtook Meta this period under time-decay credit. Meta prospecting efficiency dipped slightly as CPMs rose. Refunds held at ${(m.refund_rate * 100).toFixed(1)}%, concentrated in first-visit packages. Recommended action: scale the top two whitelisted creators' budgets 20% and rebuild the Meta prospecting audience stack.`,
  apex_fitness: (m) =>
    `Membership revenue attributed to paid media totaled $${m.revenue.toLocaleString()} for ${LATEST_MONTH} on $${m.spend.toLocaleString()} spend. Meta lead-gen forms remain the strongest cost-per-join channel. The January promo cohort's elevated refunds have fully washed out — true ROI (net of refunds, Stripe-verified) is now within 6% of gross ROI. Google Performance Max is drifting toward brand queries; consider adding negative keywords to force incremental reach.`,
  harbor_legal: (m) =>
    `Qualified matter pipeline attributed to marketing reached $${m.revenue.toLocaleString()} in ${LATEST_MONTH} (W-shape model, 90-day lookback). LinkedIn cost per lead is 3.4x Google's, but average matter value on LinkedIn-sourced deals is 6x higher — on pipeline-weighted ROI, LinkedIn is the firm's best-performing channel and spend was scaled +5% again this month. Note: collected revenue trails pipeline substantially due to the long engagement cycle; this is expected for this vertical.`,
  greentrail_outfitters: (m) =>
    `Store revenue attributed to paid channels hit $${m.revenue.toLocaleString()} in ${LATEST_MONTH} on $${m.spend.toLocaleString()} spend (last-touch). The spring gear launch scaled Meta prospecting without efficiency loss, and Google Shopping held above 4.5x. TikTok remains below blended breakeven at 1.8-2.2x but has improved every month since launch; its assisted-conversion share suggests last-touch understates it. Recommended action: run a 30-day u-shape comparison for TikTok before the next budget review. Refunds at ${(m.refund_rate * 100).toFixed(1)}% are normal for the category.`,
};

const FINDINGS = {
  summit_roofing: [
    "Google Search cost per closed job fell 14% month-over-month, the third straight decline.",
    "58% of Google-closed deals had a Meta first touch — U-shape credit now reflects it.",
    "Storm-damage keyword group converts 2.1x better than the evergreen roofing group.",
  ],
  luxe_medspa: [
    "TikTok ROAS overtook Meta for the first time (creator whitelisting program).",
    "Meta prospecting CPM rose 11%; efficiency dipped but retargeting held steady.",
    "Refunds concentrated in first-visit packages; membership upgrades refund near zero.",
  ],
  apex_fitness: [
    "Meta lead-gen forms delivered the lowest cost-per-join for the fifth straight month.",
    "January promo refund wave fully washed out — true ROI within 6% of gross ROI.",
    "Performance Max is absorbing brand queries; incrementality is likely overstated.",
  ],
  harbor_legal: [
    "LinkedIn-sourced matters average 6x the value of Google-sourced matters.",
    "Pipeline-weighted ROI ranks LinkedIn first despite 3.4x higher cost per lead.",
    "Long collection cycle: 38% of attributed pipeline collected to date (expected).",
  ],
  greentrail_outfitters: [
    "Spring launch scaled Meta spend +2%/mo with no ROAS degradation.",
    "Google Shopping sustained above 4.5x for the full quarter.",
    "TikTok improved every month but sits below breakeven under last-touch credit.",
  ],
};

// ── Public dataset assembly ──────────────────────────────────────────────────

export function buildDemoData() {
  const clients = [];
  const reports = {};
  const seriesByClient = {};

  for (const p of CLIENT_PROFILES) {
    const series = buildSeries(p);
    seriesByClient[p.client_id] = series;

    const channelIds = Object.keys(p.channels);
    clients.push({
      client_id: p.client_id,
      client_name: p.client_name,
      client_display_name: p.client_name,
      vertical: p.vertical,
      contact: p.contact,
      agency_id: p.agency_id,
      attribution_model: p.attribution_model,
      lookback_days: p.lookback_days,
      client_report_email: p.client_report_email,
      meta_enabled: channelIds.includes("meta"),
      google_ads_enabled: channelIds.includes("google_ads"),
      linkedin_ads_enabled: channelIds.includes("linkedin_ads"),
      tiktok_ads_enabled: channelIds.includes("tiktok_ads"),
      hubspot_enabled: !!p.sources.hubspot,
      stripe_enabled: !!p.sources.stripe,
      meta_secret_configured: channelIds.includes("meta"),
      google_ads_secret_configured: channelIds.includes("google_ads"),
      linkedin_ads_secret_configured: channelIds.includes("linkedin_ads"),
      tiktok_secret_configured: channelIds.includes("tiktok_ads"),
      hubspot_secret_configured: !!p.sources.hubspot,
      stripe_secret_configured: !!p.sources.stripe,
      databricks_schema: `workspace.attribution_${p.client_id}`,
      story: p.story,
    });

    // One insight report per month, latest first — mirrors InsightReportResponse.
    reports[p.client_id] = MONTHS.slice()
      .reverse()
      .map((month, idx) => {
        const m = monthTotals(series, month);
        const collected = round(m.revenue * p.collect_rate);
        const refundRate = p.refund_rate;
        const trueRoi = m.spend ? (collected * (1 - refundRate)) / m.spend : 0;
        const latest = month === LATEST_MONTH;
        return {
          report_id: `rpt_${p.client_id}_${month.replace("-", "")}`,
          client_id: p.client_id,
          agency_id: p.agency_id,
          report_month: month,
          narrative: latest
            ? NARRATIVES[p.client_id]({ ...m, refund_rate: refundRate })
            : `Monthly attribution summary for ${p.client_name}: $${m.revenue.toLocaleString()} attributed pipeline on $${m.spend.toLocaleString()} spend across ${m.rows.length} active channels.`,
          key_findings: latest ? FINDINGS[p.client_id] : [],
          top_channel: channelLabel(topChannel(m.rows)),
          total_pipeline: m.revenue,
          total_spend: m.spend,
          overall_roi: m.spend ? m.revenue / m.spend : 0,
          collected_revenue: collected,
          refund_rate: refundRate,
          true_roi: trueRoi,
          attribution_model: p.attribution_model,
          generated_at: `${month}-28T09:04:00Z`,
          run_id: `demo${(idx + 1).toString().padStart(3, "0")}`,
          prompt_version: "executive-reporting@2.3.0",
          model_id: "claude-sonnet-5",
        };
      });
  }

  return { clients, reports, seriesByClient };
}

export function demoAgency() {
  return {
    agency_id: "n8iv_promotions",
    agency_name: "N8iV Promotions",
    brand_color: "#2563EB",
    sender_name: "N8iV Promotions Analytics",
  };
}

export { CLIENT_PROFILES, channelLabel };
