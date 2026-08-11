type StatementColumn = {
  name: string;
};

type StatementResponse = {
  statement_id?: string;
  status?: {
    state?: string;
    error?: {
      message?: string;
    };
  };
  manifest?: {
    schema?: {
      columns?: StatementColumn[];
    };
  };
  result?: {
    data_array?: unknown[][];
  };
};

function clean(value: string | undefined) {
  return (value || "").trim();
}

export function databricksHost() {
  const raw = clean(process.env.DATABRICKS_SERVER_HOSTNAME || process.env.DATABRICKS_HOST);
  return raw.replace(/^https?:\/\//, "").replace(/\/$/, "");
}

function warehouseId() {
  const explicit = clean(process.env.DATABRICKS_WAREHOUSE_ID);
  if (explicit) {
    return explicit;
  }
  const httpPath = clean(process.env.DATABRICKS_HTTP_PATH);
  const match = httpPath.match(/warehouses\/([^/]+)/);
  return match?.[1] || "";
}

export function opsSchema() {
  return clean(process.env.ATTRIBUTION_OPS_SCHEMA) || "workspace.attribution_ops";
}

export function hasDatabricksConfig() {
  return Boolean(databricksHost() && warehouseId() && clean(process.env.DATABRICKS_TOKEN));
}

export function hasDatabricksApiConfig() {
  return Boolean(databricksHost() && clean(process.env.DATABRICKS_TOKEN));
}

export async function requestDatabricks<T>(path: string, init?: RequestInit): Promise<T> {
  const host = databricksHost();
  const token = clean(process.env.DATABRICKS_TOKEN);
  if (!host || !token) {
    throw new Error("Databricks API environment variables are not configured.");
  }
  const response = await fetch(`https://${host}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...(init?.headers || {})
    },
    cache: "no-store"
  });
  if (!response.ok) {
    const responseText = await response.text();
    throw new Error(`Databricks API returned ${response.status}: ${responseText.slice(0, 300)}`);
  }
  return response.json() as Promise<T>;
}

async function requestStatement(path: string, init?: RequestInit): Promise<StatementResponse> {
  return requestDatabricks<StatementResponse>(path, init);
}

async function waitForStatement(statementId: string) {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const payload = await requestStatement(`/api/2.0/sql/statements/${statementId}`);
    const state = payload.status?.state || "";
    if (state === "SUCCEEDED") {
      return payload;
    }
    if (state === "FAILED" || state === "CANCELED" || state === "CLOSED") {
      throw new Error(payload.status?.error?.message || `Databricks statement ${state}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 350 + attempt * 150));
  }
  throw new Error("Databricks statement timed out.");
}

export async function runSql<T extends Record<string, unknown>>(statement: string): Promise<T[]> {
  if (!hasDatabricksConfig()) {
    throw new Error("Databricks environment variables are not configured.");
  }

  const payload = await requestStatement("/api/2.0/sql/statements", {
    method: "POST",
    body: JSON.stringify({
      warehouse_id: warehouseId(),
      statement,
      wait_timeout: "10s",
      disposition: "INLINE",
      format: "JSON_ARRAY"
    })
  });

  const finalPayload =
    payload.status?.state === "SUCCEEDED"
      ? payload
      : await waitForStatement(String(payload.statement_id || ""));

  const columns = finalPayload.manifest?.schema?.columns?.map((column) => column.name) || [];
  const rows = finalPayload.result?.data_array || [];

  return rows.map((row) => {
    return Object.fromEntries(columns.map((column, index) => [column, row[index]])) as T;
  });
}
