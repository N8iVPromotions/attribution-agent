import { NextResponse } from "next/server";
import { hasDatabricksConfig } from "@/lib/databricks";
import { hasPipelineExecutionConfig } from "@/lib/cloud-run";
import { hasControlApiConfig } from "@/lib/control-api";
import { hasTenantLifecycleConfig } from "@/lib/tenant-lifecycle";

export async function GET() {
  return NextResponse.json({
    ok: true,
    app: "arie-command-center",
    databricksConfigured: hasDatabricksConfig(),
    pipelineExecutionConfigured: hasPipelineExecutionConfig(),
    controlApiConfigured: hasControlApiConfig(),
    clientConfigurationConfigured: hasControlApiConfig(),
    tenantLifecycleConfigured: hasTenantLifecycleConfig(),
    runtime: "vercel"
  }, { headers: { "Cache-Control": "no-store" } });
}
