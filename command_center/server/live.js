// Live-mode bridge to the ARIE FastAPI service on Cloud Run.
//
// The Cloud Run service is private (org policy blocks allUsers), so every
// request carries two credentials:
//   1. A Google-signed ID token for the service URL (Cloud Run IAM layer),
//      minted from the service-account key in the GCP_SA_KEY secret.
//   2. The ARIE admin API key in X-API-Key (application RBAC layer).
//
// Set ARIE_API_PUBLIC=true to skip the ID token if the service is ever
// deployed with --allow-unauthenticated.

import { GoogleAuth } from "google-auth-library";

const API_BASE = (process.env.ARIE_API_BASE || "").replace(/\/+$/, "");
const API_KEY = process.env.ARIE_API_KEY || "";
const PUBLIC_API = process.env.ARIE_API_PUBLIC === "true";

let idTokenClient = null;

export function liveConfigured() {
  if (!API_BASE || !API_KEY) return false;
  return PUBLIC_API || !!process.env.GCP_SA_KEY;
}

export function liveConfigHint() {
  const missing = [];
  if (!API_BASE) missing.push("ARIE_API_BASE");
  if (!API_KEY) missing.push("ARIE_API_KEY");
  if (!PUBLIC_API && !process.env.GCP_SA_KEY) missing.push("GCP_SA_KEY");
  return missing;
}

async function getIdToken() {
  if (PUBLIC_API) return null;
  if (!idTokenClient) {
    const credentials = JSON.parse(process.env.GCP_SA_KEY);
    const auth = new GoogleAuth({ credentials });
    idTokenClient = await auth.getIdTokenClient(API_BASE);
  }
  const headers = await idTokenClient.getRequestHeaders();
  return headers.get
    ? headers.get("Authorization")
    : headers["Authorization"] || headers.authorization;
}

export async function arieFetch(path, { method = "GET", body } = {}) {
  if (!liveConfigured()) {
    const missing = liveConfigHint().join(", ");
    const err = new Error(`Live mode is not configured. Missing secrets: ${missing}`);
    err.status = 503;
    throw err;
  }
  const headers = { "X-API-Key": API_KEY, "X-Actor": "command-center" };
  const idToken = await getIdToken();
  if (idToken) headers["Authorization"] = idToken;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text.slice(0, 300) };
  }
  if (!res.ok) {
    const err = new Error(
      typeof data?.detail === "string" ? data.detail : `ARIE API returned ${res.status}`
    );
    err.status = res.status;
    throw err;
  }
  return data;
}
