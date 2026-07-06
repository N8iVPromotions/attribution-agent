# N8iV Promotions × Attribution Agent — Unified Strategic Playbook

Packaging a digital marketing agency with a proprietary closed-loop attribution
engine. Prepared for customer-facing demos, website integration, and
promotional content. (Source: Unified Strategic Playbook v1.0, July 2026.)

The demo environment described in sections 3–4 is implemented in this repo —
see [Running the demo environment](#running-the-demo-environment) at the end.

## 1. Core messaging & positioning: the Brain & Body model

The agency services and the software asset are positioned as one seamless
offering, not separate entities:

- **N8iV Promotions (the Body)** — the strategic execution layer: high-intent
  SEO keyword strategies, optimized content pipelines, performance campaigns,
  data warehouse configuration.
- **Attribution Agent / ARIE (the Brain)** — the technical engine: identity
  resolution, cross-channel touchpoint logging, server-side webhook
  consumption, closed-loop multi-touch revenue attribution.

Coupled together, N8iV Promotions moves from commodity service agency to
high-leverage technology partner: not selling hourly output, but guaranteed
visibility into revenue attribution.

## 2. Website architecture & customer funnel

### 2.1 Homepage structural hierarchy

1. **Hero section** — business outcomes only.
   Headline: *"Full-Service Digital Marketing Powered by Closed-Loop Revenue
   Intelligence."*
   Subheadline: *"We don't optimize for clicks, impressions, or vanity
   metrics. We deploy proprietary attribution engines to map every marketing
   dollar directly to a closed deal."*
2. **The modern marketing blind spot** — visualize the discrepancy between
   standard ad-manager reports (Facebook/Google claiming duplicate
   conversions) and actual bottom-line revenue.
3. **Introducing ARIE** — a high-impact product card introducing the Automatic
   Revenue Intelligence Engine as N8iV's unfair advantage.

### 2.2 Dedicated product page

Styled like a B2B SaaS landing page, targeting tech-forward marketing
directors and founders. Feature highlights: cookie-less first-party ID
resolution, offline conversion sync via server webhooks, multi-touch
fractional attribution modeling.

## 3. Technical flow for the demo environment

A lightweight, self-contained mock data pipeline that simulates a real
closed-loop lifecycle for a non-technical prospect:

1. **Traffic acquisition (touchpoint 1)** — a fictional user clicks an organic
   SEO blog post targeting a high-intent keyword. The click script logs the
   click, anonymous ID, UTM parameters, and timestamp to a local database.
2. **Nurturing (touchpoint 2)** — the user returns via an Instagram
   promotional link three days later, updating the first-party identity graph.
3. **Conversion event (lead form)** — the user fills out a consultation form,
   linking the anonymous tracking history to a clear email identity.
4. **CRM closed-won (the loop closes)** — a mock CRM (Stripe/HubSpot
   simulator) fires a "Deal Won" webhook for $15,000. The engine intercepts
   it, runs matching logic on the email identity, and backfills the entire
   marketing timeline with fractional revenue credit.

### Mock database schema

| Table | Key fields | Mock data sample | Purpose in demo |
|---|---|---|---|
| `arie_click_logs` | anon_id, utm_source, utm_campaign, occurred_at | `anon_881 \| google \| seo_guide \| 2026-07-01` | Logs the original click asset |
| `arie_identity_map` | anon_id, resolved_email, updated_at | `anon_881 \| ceo@techcorp.com \| 2026-07-04` | Resolves anonymous to known identity |
| `crm_deal_events` | email, deal_amount, status, occurred_at | `ceo@techcorp.com \| $15,000 \| CLOSED_WON \| 2026-07-05` | Simulates the external invoice/revenue win |

## 4. Step-by-step customer-facing demo playbook

**Step 1 — Establishing the blind spot (the hook).** Open a dummy analytics
dashboard showing high bounce rates and multi-channel attribution chaos. Ask:
*"If you look at your dashboard right now, can you tell me exactly which
organic blog post or social post led to your largest closed contract this
month? No. It looks like a massive pool of disconnected clicks."*

**Step 2 — Triggering the live attribution (the magic).** Trigger a mock form
submission or CRM deal close in front of the prospect using the control
panel. Show the database view updating instantly to bind the raw click to the
$15,000 deal. Emphasize: *"ARIE just instantly pulled the historical
interaction from 5 days ago and matched it to a real bank deposit. This is
the truth."*

**Step 3 — The retainer transition (the pitch).** Bring up the N8iV campaign
dashboard. Conclude: *"Because our agency operates with ARIE running in the
background, our execution team isn't guessing what content to write or what
keywords to target next week. We look at this ledger, see exactly what drives
cold, hard cash, and multiply it."*

## Running the demo environment

The blueprint above is implemented under
`attribution_agent/attribution_agent/demo/` — fully self-contained (SQLite
in-memory, no Databricks, no live APIs, no credentials):

- `demo_store.py` — the three-table mock database
- `demo_engine.py` — lifecycle simulator + closed-loop matching/backfill
  (reuses the production `attribution_models.allocate_credit`, so all six
  multi-touch models are selectable live)
- `demo_app.py` — the customer-facing Streamlit control panel

```bash
cd attribution_agent/attribution_agent
streamlit run demo/demo_app.py
```

The control panel exposes each lifecycle step as a button (SEO click →
Instagram return → consultation form → $15,000 deal-won webhook), a
"run full scripted journey" shortcut, live views of the three mock tables,
and the backfilled attribution timeline with a model selector. Reset between
demos with the "Reset demo data" button.

The demo app is deliberately separate from the internal Command Center
(`app.py`) so a prospect never sees internal client data.
