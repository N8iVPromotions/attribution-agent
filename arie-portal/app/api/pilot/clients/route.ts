import { NextRequest, NextResponse } from "next/server";
import { controlApiFetch, hasControlApiConfig } from "@/lib/control-api";
import type {
  ClientPlatformKey,
  PilotClientConfiguration,
  PilotClientConfigurationResult
} from "@/lib/types";

export const runtime = "nodejs";

const models = new Set(["last_touch"]);
const clientIdPattern = /^[a-z][a-z0-9_]{0,62}$/;
const accountIdPattern = /^[A-Za-z0-9:_-]{0,180}$/;
const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const defaultHubspotClosedWonStageIds = ["closedwon", "won"];

type PlatformInput = {
  enabled?: unknown;
  accountId?: unknown;
  credential?: unknown;
  expiresAt?: unknown;
};

type ControlClient = {
  client_id: string;
  client_name: string;
  agency_id: string;
  attribution_model: string;
  reporting_currency: string;
  client_report_email: string;
  lookback_days: number;
  databricks_schema: string;
  meta_enabled: boolean;
  meta_ad_account_id: string;
  meta_token_expires_at: string;
  meta_secret_configured: boolean;
  google_ads_enabled: boolean;
  google_ads_customer_id: string;
  google_ads_token_expires_at: string;
  google_ads_secret_configured: boolean;
  linkedin_ads_enabled: boolean;
  linkedin_ads_account_id: string;
  linkedin_token_expires_at: string;
  linkedin_ads_secret_configured: boolean;
  tiktok_ads_enabled: boolean;
  tiktok_ads_advertiser_id: string;
  tiktok_token_expires_at: string;
  tiktok_secret_configured: boolean;
  hubspot_enabled: boolean;
  hubspot_pipeline_id: string;
  hubspot_closed_won_stage_ids: string[];
  hubspot_token_expires_at: string;
  hubspot_secret_configured: boolean;
  stripe_enabled: boolean;
  stripe_account_id: string;
  stripe_history_start_date: string;
  stripe_token_expires_at: string;
  stripe_secret_configured: boolean;
};

function text(value: unknown, maxLength = 500) {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : "";
}

function boolean(value: unknown) {
  return value === true;
}

function platform(body: Record<string, unknown>, key: ClientPlatformKey): PlatformInput {
  const platforms = body.platforms;
  if (!platforms || typeof platforms !== "object" || Array.isArray(platforms)) return {};
  const value = (platforms as Record<string, unknown>)[key];
  return value && typeof value === "object" && !Array.isArray(value) ? (value as PlatformInput) : {};
}

function accountId(value: unknown, label: string) {
  const result = text(value, 180);
  if (result && !accountIdPattern.test(result)) {
    throw new Error(`${label} contains unsupported characters.`);
  }
  return result;
}

function credential(value: unknown) {
  const result = text(value, 16_384);
  if (result.includes("\0")) throw new Error("A credential contains an invalid character.");
  return result;
}

function expiry(value: unknown) {
  const result = text(value, 10);
  if (result && !/^\d{4}-\d{2}-\d{2}$/.test(result)) {
    throw new Error("Credential expiry dates must use YYYY-MM-DD.");
  }
  return result;
}

function historyStartDate(value: unknown) {
  const result = text(value, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result)) {
    throw new Error("Stripe history start date is required and must use YYYY-MM-DD.");
  }
  const parsed = new Date(`${result}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== result) {
    throw new Error("Stripe history start date must be a valid calendar date.");
  }
  if (result > new Date().toISOString().slice(0, 10)) {
    throw new Error("Stripe history start date cannot be in the future.");
  }
  return result;
}

function hubspotClosedWonStageIds(value: unknown): string[] {
  const rawValues = value === undefined
    ? defaultHubspotClosedWonStageIds
    : Array.isArray(value)
      ? value
      : typeof value === "string"
        ? value.split(/[\n,]+/)
        : [];
  if (!rawValues.length || rawValues.length > 20) {
    throw new Error("Provide between 1 and 20 HubSpot closed-won stage IDs.");
  }

  const stageIds: string[] = [];
  for (const rawValue of rawValues) {
    if (typeof rawValue !== "string" || rawValue.length > 100) {
      throw new Error("Each HubSpot closed-won stage ID must be at most 100 characters.");
    }
    const normalized = rawValue.trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
    if (!normalized) {
      throw new Error("HubSpot closed-won stage IDs cannot be blank.");
    }
    if (!stageIds.includes(normalized)) stageIds.push(normalized);
  }
  return stageIds;
}

function safeResponse(client: ControlClient): PilotClientConfiguration {
  return {
    clientId: client.client_id,
    clientName: client.client_name,
    agencyId: client.agency_id,
    attributionModel: client.attribution_model,
    reportingCurrency: client.reporting_currency || "USD",
    reportEmail: client.client_report_email,
    lookbackDays: Number(client.lookback_days || 30),
    stripeHistoryStartDate: client.stripe_history_start_date || "",
    hubspotClosedWonStageIds: hubspotClosedWonStageIds(
      client.hubspot_closed_won_stage_ids
    ),
    databricksSchema: client.databricks_schema,
    platforms: {
      meta: {
        enabled: Boolean(client.meta_enabled),
        accountId: client.meta_ad_account_id || "",
        credentialConfigured: Boolean(client.meta_secret_configured),
        expiresAt: client.meta_token_expires_at || ""
      },
      google: {
        enabled: Boolean(client.google_ads_enabled),
        accountId: client.google_ads_customer_id || "",
        credentialConfigured: Boolean(client.google_ads_secret_configured),
        expiresAt: client.google_ads_token_expires_at || ""
      },
      linkedin: {
        enabled: Boolean(client.linkedin_ads_enabled),
        accountId: client.linkedin_ads_account_id || "",
        credentialConfigured: Boolean(client.linkedin_ads_secret_configured),
        expiresAt: client.linkedin_token_expires_at || ""
      },
      tiktok: {
        enabled: Boolean(client.tiktok_ads_enabled),
        accountId: client.tiktok_ads_advertiser_id || "",
        credentialConfigured: Boolean(client.tiktok_secret_configured),
        expiresAt: client.tiktok_token_expires_at || ""
      },
      hubspot: {
        enabled: Boolean(client.hubspot_enabled),
        accountId: client.hubspot_pipeline_id || "",
        credentialConfigured: Boolean(client.hubspot_secret_configured),
        expiresAt: client.hubspot_token_expires_at || ""
      },
      stripe: {
        enabled: Boolean(client.stripe_enabled),
        accountId: client.stripe_account_id || "",
        credentialConfigured: Boolean(client.stripe_secret_configured),
        expiresAt: client.stripe_token_expires_at || ""
      }
    }
  };
}

export async function GET() {
  if (!hasControlApiConfig()) {
    return NextResponse.json(
      { ok: false, error: "Secure client configuration is not connected to the ARIE control API." },
      { status: 503, headers: { "Cache-Control": "private, no-store" } }
    );
  }

  try {
    const clients = await controlApiFetch<ControlClient[]>("/api/v1/clients");
    return NextResponse.json(
      { ok: true, clients: clients.map(safeResponse) },
      { headers: { "Cache-Control": "private, no-store" } }
    );
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        error: error instanceof Error ? error.message : "Could not reach the ARIE control API."
      },
      { status: 502, headers: { "Cache-Control": "private, no-store" } }
    );
  }
}

export async function POST(request: NextRequest) {
  if (!hasControlApiConfig()) {
    return NextResponse.json(
      { ok: false, error: "Secure client configuration is not connected to the ARIE control API." },
      { status: 503, headers: { "Cache-Control": "private, no-store" } }
    );
  }

  try {
    const body = (await request.json()) as Record<string, unknown>;
    const existingClientId = text(body.clientId, 63);
    const clientName = text(body.clientName, 120);
    const agencyId = text(body.agencyId, 63);
    const reportEmail = text(body.reportEmail, 254).toLowerCase();
    const attributionModel = text(body.attributionModel, 30) || "last_touch";
    const lookbackDays = Number(body.lookbackDays || 30);

    if (existingClientId && !clientIdPattern.test(existingClientId)) throw new Error("Client ID is invalid.");
    if (clientName.length < 2) throw new Error("Business name is required.");
    if (!clientIdPattern.test(agencyId)) throw new Error("Select a valid agency.");
    if (!emailPattern.test(reportEmail)) throw new Error("A valid report email is required.");
    if (!models.has(attributionModel)) {
      throw new Error("Production client configuration supports only CRM Source Match.");
    }
    if (!Number.isInteger(lookbackDays) || lookbackDays < 7 || lookbackDays > 365) {
      throw new Error("Lookback must be between 7 and 365 days.");
    }

    const meta = platform(body, "meta");
    const google = platform(body, "google");
    const linkedin = platform(body, "linkedin");
    const tiktok = platform(body, "tiktok");
    const hubspot = platform(body, "hubspot");
    const stripe = platform(body, "stripe");
    const stripeEnabled = boolean(stripe.enabled);
    const stripeHistoryInput = text(body.stripeHistoryStartDate, 10);
    const stripeHistoryStartDate = stripeEnabled || stripeHistoryInput
      ? historyStartDate(stripeHistoryInput)
      : "";

    const payload = {
      client_name: clientName,
      client_display_name: clientName,
      agency_id: agencyId,
      client_report_email: reportEmail,
      attribution_model: attributionModel,
      reporting_currency: "USD",
      lookback_days: lookbackDays,
      databricks_schema: "",
      meta_enabled: boolean(meta.enabled),
      meta_ad_account_id: accountId(meta.accountId, "Meta ad account ID"),
      meta_access_token: credential(meta.credential),
      meta_token_expires_at: expiry(meta.expiresAt),
      google_ads_enabled: boolean(google.enabled),
      google_ads_customer_id: accountId(google.accountId, "Google Ads customer ID"),
      google_ads_refresh_token: credential(google.credential),
      google_ads_token_expires_at: expiry(google.expiresAt),
      linkedin_ads_enabled: boolean(linkedin.enabled),
      linkedin_ads_account_id: accountId(linkedin.accountId, "LinkedIn ad account ID"),
      linkedin_access_token: credential(linkedin.credential),
      linkedin_token_expires_at: expiry(linkedin.expiresAt),
      tiktok_ads_enabled: boolean(tiktok.enabled),
      tiktok_ads_advertiser_id: accountId(tiktok.accountId, "TikTok advertiser ID"),
      tiktok_access_token: credential(tiktok.credential),
      tiktok_token_expires_at: expiry(tiktok.expiresAt),
      hubspot_enabled: boolean(hubspot.enabled),
      hubspot_pipeline_id: accountId(hubspot.accountId, "HubSpot pipeline ID"),
      hubspot_closed_won_stage_ids: hubspotClosedWonStageIds(
        body.hubspotClosedWonStageIds
      ),
      hubspot_access_token: credential(hubspot.credential),
      hubspot_token_expires_at: expiry(hubspot.expiresAt),
      stripe_enabled: stripeEnabled,
      stripe_account_id: accountId(stripe.accountId, "Stripe account ID"),
      stripe_history_start_date: stripeHistoryStartDate,
      stripe_secret_key: credential(stripe.credential),
      stripe_token_expires_at: expiry(stripe.expiresAt)
    };

    const client = await controlApiFetch<ControlClient>(
      existingClientId
        ? `/api/v1/clients/${encodeURIComponent(existingClientId)}`
        : "/api/v1/clients",
      {
        method: existingClientId ? "PUT" : "POST",
        body: JSON.stringify(payload)
      }
    );
    const result: PilotClientConfigurationResult = {
      ok: true,
      message: existingClientId
        ? "Client configuration updated. New credentials were stored as fresh Secret Manager versions."
        : "Pilot client created. Credentials were stored in GCP Secret Manager.",
      client: safeResponse(client)
    };
    return NextResponse.json(result, {
      status: existingClientId ? 200 : 201,
      headers: { "Cache-Control": "private, no-store" }
    });
  } catch (error) {
    const result: PilotClientConfigurationResult = {
      ok: false,
      message: "Pilot configuration was not saved.",
      error: error instanceof Error ? error.message : "Pilot configuration failed."
    };
    return NextResponse.json(result, {
      status: 400,
      headers: { "Cache-Control": "private, no-store" }
    });
  }
}
