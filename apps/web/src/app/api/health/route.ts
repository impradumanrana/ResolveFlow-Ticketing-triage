import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const baseUrl = process.env.AI_API_BASE_URL;
  if (!baseUrl) {
    return NextResponse.json(
      { status: "degraded", web: "ok", aiApi: "not-configured" },
      { status: 503 },
    );
  }

  try {
    const response = await fetch(new URL("/healthz", baseUrl), {
      cache: "no-store",
      signal: AbortSignal.timeout(2500),
    });
    return NextResponse.json(
      { status: response.ok ? "ok" : "degraded", web: "ok", aiApi: response.ok ? "ok" : "unhealthy" },
      { status: response.ok ? 200 : 503 },
    );
  } catch {
    return NextResponse.json(
      { status: "degraded", web: "ok", aiApi: "unreachable" },
      { status: 503 },
    );
  }
}
