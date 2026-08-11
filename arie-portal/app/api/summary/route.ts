import { NextResponse } from "next/server";
import { getCommandCenterData } from "@/lib/data";

export async function GET() {
  const data = await getCommandCenterData();
  return NextResponse.json(data, { headers: { "Cache-Control": "private, no-store" } });
}
