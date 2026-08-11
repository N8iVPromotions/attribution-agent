import { NextRequest, NextResponse } from "next/server";
import { hasTenantLifecycleConfig, triggerTenantLifecycle } from "@/lib/tenant-lifecycle";
import type { TenantLifecycleCommand, TenantLifecycleInput } from "@/lib/types";

const commands = new Set<TenantLifecycleCommand>([
  "create_agency",
  "create_business",
  "delete_business",
  "delete_agency"
]);
const models = new Set(["last_touch", "first_touch", "linear", "time_decay", "u_shape", "w_shape"]);
const slugPattern = /^[a-z][a-z0-9_]{0,62}$/;
const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const protectedIds = new Set(["demo_agency", "demo_client", "n8iv_promotions"]);

function field(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function validate(body: Record<string, unknown>): TenantLifecycleInput {
  const command = field(body.command) as TenantLifecycleCommand;
  const entityId = field(body.entityId);
  const entityName = field(body.entityName);
  const agencyId = field(body.agencyId);
  const reportEmail = field(body.reportEmail).toLowerCase();
  const attributionModel = field(body.attributionModel) || "last_touch";
  const confirmation = typeof body.confirmation === "string" ? body.confirmation : "";

  if (!commands.has(command)) throw new Error("Unsupported tenant lifecycle command.");
  if (!slugPattern.test(entityId)) throw new Error("Entity ID must be a canonical lowercase slug.");
  if (command.startsWith("create_") && (entityName.length < 2 || entityName.length > 120)) {
    throw new Error("Name must be between 2 and 120 characters.");
  }
  if (command === "create_business") {
    if (!slugPattern.test(agencyId)) throw new Error("A valid agency is required.");
    if (reportEmail && !emailPattern.test(reportEmail)) throw new Error("Report email is not valid.");
    if (!models.has(attributionModel)) throw new Error("Attribution model is not valid.");
  }
  if (command.startsWith("delete_")) {
    if (protectedIds.has(entityId)) throw new Error("This protected production or demo entity cannot be deleted.");
    const noun = command === "delete_agency" ? "AGENCY" : "BUSINESS";
    const expected = `DELETE ${noun} ${entityId}`;
    if (confirmation !== expected) throw new Error(`Enter the exact confirmation: ${expected}`);
  }

  return { command, entityId, entityName, agencyId, reportEmail, attributionModel, confirmation };
}

export async function POST(request: NextRequest) {
  if (!hasTenantLifecycleConfig()) {
    return NextResponse.json(
      { ok: false, error: "Tenant lifecycle execution is not configured." },
      { status: 503 }
    );
  }
  let input: TenantLifecycleInput;
  try {
    input = validate((await request.json()) as Record<string, unknown>);
  } catch (error) {
    return NextResponse.json(
      { ok: false, error: error instanceof Error ? error.message : "Lifecycle request is invalid." },
      { status: 400 }
    );
  }
  try {
    const requestId = crypto.randomUUID();
    const requestedBy = field(process.env.ARIE_BASIC_AUTH_USER) || "arie-command-center";
    const result = await triggerTenantLifecycle(input, requestId, requestedBy.slice(0, 200));
    return NextResponse.json(result, { status: 202 });
  } catch (error) {
    return NextResponse.json(
      { ok: false, error: error instanceof Error ? error.message : "Lifecycle command failed." },
      { status: 502 }
    );
  }
}
