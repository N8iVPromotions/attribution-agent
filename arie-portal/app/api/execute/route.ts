import { NextRequest, NextResponse } from "next/server";
import { triggerPipelineRun } from "@/lib/cloud-run";

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}));
  const result = await triggerPipelineRun({
    agencyId: String(body.agencyId || "demo_agency"),
    clientIds: Array.isArray(body.clientIds) ? body.clientIds.map(String) : [],
    attributionModel: String(body.attributionModel || "last_touch"),
    dryRun: body.dryRun !== false,
    runMode: String(body.runMode || "agency")
  });

  return NextResponse.json(result, { status: result.ok ? 200 : 202 });
}
