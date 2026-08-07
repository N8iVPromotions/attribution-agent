import { NextResponse } from "next/server";
import { hasDatabricksConfig } from "@/lib/databricks";

export async function GET() {
  return NextResponse.json({
    ok: true,
    app: "arie-command-center",
    databricksConfigured: hasDatabricksConfig(),
    runtime: "vercel"
  });
}
