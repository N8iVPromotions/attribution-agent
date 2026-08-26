import { getCloudRunIdToken, hasGcpCloudRunIdentityConfig } from "@/lib/gcp-cloud-run";

function clean(value: string | undefined) {
  return (value || "").trim().replace(/\/+$/, "");
}

function isCloudRunUrl(value: string) {
  try {
    const hostname = new URL(value).hostname.toLowerCase();
    return hostname.endsWith(".run.app") || hostname.endsWith(".a.run.app");
  } catch {
    return false;
  }
}

export function hasControlApiConfig() {
  const base = clean(process.env.ARIE_API_BASE);
  const apiKey = clean(process.env.ARIE_API_KEY);
  return Boolean(base && apiKey && (!isCloudRunUrl(base) || hasGcpCloudRunIdentityConfig()));
}

export async function controlApiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const base = clean(process.env.ARIE_API_BASE);
  const apiKey = clean(process.env.ARIE_API_KEY);
  if (!base || !apiKey) {
    throw new Error("ARIE control API is not configured.");
  }

  const cloudRunIdToken = isCloudRunUrl(base) ? await getCloudRunIdToken(base) : "";

  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "x-actor": "arie-command-center",
      ...(cloudRunIdToken ? { authorization: `Bearer ${cloudRunIdToken}` } : {}),
      ...(init.headers || {})
    },
    cache: "no-store"
  });
  const payload = (await response.json().catch(() => ({}))) as T & { detail?: string };
  if (!response.ok) {
    throw new Error(payload.detail || `ARIE control API returned HTTP ${response.status}.`);
  }
  return payload;
}
