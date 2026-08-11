import { NextRequest, NextResponse } from "next/server";
import { triggerPipelineRun } from "@/lib/cloud-run";

const models = new Set(["last_touch", "first_touch", "linear", "time_decay", "u_shape", "w_shape"]);
const identifier = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/;

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
  const agencyId = String(body.agencyId || "");
  const clientIds = Array.isArray(body.clientIds) ? body.clientIds.map(String) : [];
  const attributionModel = String(body.attributionModel || "last_touch");
  const dryRun = body.dryRun !== false;

  if (!identifier.test(agencyId) || clientIds.length > 50 || clientIds.some((id) => !identifier.test(id))) {
    return NextResponse.json({ ok: false, message: "Invalid agency or client selection." }, { status: 400 });
  }
  if (!models.has(attributionModel)) {
    return NextResponse.json({ ok: false, message: "Unsupported attribution model." }, { status: 400 });
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
    dryRun,
    runMode: clientIds.length === 1 ? "client" : "agency",
    confirmation: typeof body.confirmation === "string" ? body.confirmation : undefined
  });

  return NextResponse.json(result, { status: result.ok ? 202 : 503 });
}
