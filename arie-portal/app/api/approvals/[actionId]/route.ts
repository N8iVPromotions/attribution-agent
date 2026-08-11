import { NextRequest, NextResponse } from "next/server";
import { controlApiFetch } from "@/lib/control-api";

const validResolutions = new Set(["approved", "rejected"]);

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ actionId: string }> }
) {
  const { actionId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as {
    resolution?: string;
    resolutionNote?: string;
  };
  const resolution = String(body.resolution || "").toLowerCase();
  if (!validResolutions.has(resolution)) {
    return NextResponse.json({ error: "Resolution must be approved or rejected." }, { status: 400 });
  }

  try {
    const payload = await controlApiFetch(`/api/v1/approvals/${encodeURIComponent(actionId)}/resolve`, {
      method: "POST",
      body: JSON.stringify({
        resolution,
        resolution_note: String(body.resolutionNote || "").slice(0, 500)
      })
    });
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Approval update failed." },
      { status: 503 }
    );
  }
}
