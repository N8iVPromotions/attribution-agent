import { NextRequest, NextResponse } from "next/server";
import { triggerPipelineRun } from "@/lib/cloud-run";

const models = new Set(["last_touch"]);
const identifier = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/;
const reportMonthPattern = /^\d{4}-(0[1-9]|1[0-2])$/;

function previousCompletedMonth() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit"
  }).formatToParts(new Date());
  const year = Number(parts.find((part) => part.type === "year")?.value);
  const month = Number(parts.find((part) => part.type === "month")?.value);
  return new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 7);
}

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
  const agencyId = String(body.agencyId || "");
  const clientIds = Array.isArray(body.clientIds) ? body.clientIds.map(String) : [];
  const attributionModel = String(body.attributionModel || "last_touch");
  const dryRun = body.dryRun !== false;
  const reportMonth = String(body.reportMonth || "");

  if (!identifier.test(agencyId) || clientIds.length > 50 || clientIds.some((id) => !identifier.test(id))) {
    return NextResponse.json({ ok: false, message: "Invalid agency or client selection." }, { status: 400 });
  }
  if (!models.has(attributionModel)) {
    return NextResponse.json({ ok: false, message: "Production currently supports only CRM source match (single-touch)." }, { status: 400 });
  }
  if (!reportMonthPattern.test(reportMonth) || reportMonth > previousCompletedMonth()) {
    return NextResponse.json({ ok: false, message: "Select a completed report month." }, { status: 400 });
  }
  if (!dryRun && body.confirmation !== "RUN LIVE") {
    return NextResponse.json(
      { ok: false, message: "Live execution requires the RUN LIVE confirmation." },
      { status: 400 }
    );
  }

  const result = await triggerPipelineRun({
    agencyId,
    clientIds,
    attributionModel,
    reportMonth,
    dryRun,
    runMode: clientIds.length === 1 ? "client" : "agency",
    confirmation: typeof body.confirmation === "string" ? body.confirmation : undefined
  });

  return NextResponse.json(result, { status: result.ok ? 202 : 503 });
}
