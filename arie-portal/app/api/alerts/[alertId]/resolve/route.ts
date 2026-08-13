import { NextResponse } from "next/server";
import { hasDatabricksConfig, opsSchema, runSql } from "@/lib/databricks";

const alertIdPattern = /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,199}$/;

function sqlLiteral(value: unknown) {
  return `'${String(value || "").replaceAll("'", "''")}'`;
}

function actor() {
  return (process.env.ARIE_BASIC_AUTH_USER || "arie-command-center").trim().slice(0, 200);
}

export async function POST(
  _request: Request,
  context: { params: Promise<{ alertId: string }> }
) {
  const { alertId } = await context.params;
  if (!alertIdPattern.test(alertId)) {
    return NextResponse.json({ ok: false, error: "Invalid alert id." }, { status: 400 });
  }
  if (!hasDatabricksConfig()) {
    return NextResponse.json(
      { ok: false, error: "Databricks is not configured for alert resolution." },
      { status: 503 }
    );
  }

  const schema = opsSchema();
  try {
    const rows = await runSql<Record<string, unknown>>(`
      SELECT alert_id, client_id, agency_id, source, run_id, category, title, status
      FROM ${schema}.operator_alerts
      WHERE alert_id = ${sqlLiteral(alertId)}
      LIMIT 1
    `);
    const alert = rows[0];
    if (!alert) {
      return NextResponse.json({ ok: false, error: "Alert not found." }, { status: 404 });
    }
    if (String(alert.status || "").toLowerCase() !== "open") {
      return NextResponse.json({ ok: true, message: "Alert was already resolved." });
    }

    await runSql(`
      UPDATE ${schema}.operator_alerts
      SET status = 'resolved'
      WHERE alert_id = ${sqlLiteral(alertId)} AND status = 'open'
    `);

    const detail = JSON.stringify({
      alert_id: alertId,
      category: String(alert.category || ""),
      source: String(alert.source || ""),
      title: String(alert.title || "")
    });
    await runSql(`
      INSERT INTO ${schema}.audit_log
      (event_id, event_time, event_type, actor, client_id, agency_id, resource,
       action, outcome, detail_json, run_id, ip_address, session_id)
      VALUES (
        ${sqlLiteral(crypto.randomUUID())},
        current_timestamp(),
        'ALERT_RESOLVED',
        ${sqlLiteral(actor())},
        ${sqlLiteral(alert.client_id)},
        ${sqlLiteral(alert.agency_id)},
        'operator_alerts',
        'resolve',
        'resolved',
        ${sqlLiteral(detail)},
        ${sqlLiteral(alert.run_id)},
        '',
        ''
      )
    `);

    return NextResponse.json({ ok: true, message: "Alert marked resolved." });
  } catch (error) {
    return NextResponse.json(
      { ok: false, error: error instanceof Error ? error.message : "Alert resolution failed." },
      { status: 502 }
    );
  }
}
