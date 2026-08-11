function clean(value: string | undefined) {
  return (value || "").trim().replace(/\/+$/, "");
}

export function hasControlApiConfig() {
  return Boolean(clean(process.env.ARIE_API_BASE) && clean(process.env.ARIE_API_KEY));
}

export async function controlApiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const base = clean(process.env.ARIE_API_BASE);
  const apiKey = clean(process.env.ARIE_API_KEY);
  if (!base || !apiKey) {
    throw new Error("ARIE control API is not configured.");
  }

  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "x-actor": "arie-command-center",
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
