// All server calls go through here so every request carries the active mode.

export async function api(path, { mode = "demo", method = "GET", body } = {}) {
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(`/api${path}${sep}mode=${mode}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data?.error || data?.detail || `Request failed (${res.status})`);
  }
  return data;
}

export const fmtMoney = (n) =>
  n == null
    ? "—"
    : n >= 1_000_000
      ? `$${(n / 1_000_000).toFixed(2)}M`
      : `$${Math.round(n).toLocaleString()}`;

export const fmtRoi = (n) => (n == null ? "—" : `${Number(n).toFixed(1)}x`);

export const fmtPct = (n) => (n == null ? "—" : `${(n * 100).toFixed(1)}%`);

export const fmtMonth = (m) => {
  if (!m) return "—";
  const [y, mo] = m.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${names[parseInt(mo, 10) - 1]} ${y}`;
};

export const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};
