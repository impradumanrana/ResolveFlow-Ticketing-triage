/**
 * A protected API route.
 *
 * Exists at C03 to demonstrate and test the enforcement path end to end:
 * guard, server-derived context, internal call, uniform denial shape. The
 * operations surfaces themselves arrive in C08.
 */

import { NextResponse } from "next/server";

import { AuthorizationError } from "@/lib/authz/authorize";
import { requireAccess } from "@/lib/auth/session";
import { callAiApi } from "@/lib/bff/ai-api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const context = await requireAccess("ai_settings.view");
    const result = await callAiApi<Record<string, unknown>>(context, {
      path: "/v1/capabilities",
    });

    return NextResponse.json(
      {
        organizationId: context.organizationId,
        role: context.role,
        requestId: result.requestId,
        capabilities: result.data,
        aiApi: result.ok ? "ok" : "unavailable",
      },
      { status: result.ok ? 200 : 503 },
    );
  } catch (error) {
    if (error instanceof AuthorizationError) {
      return NextResponse.json(
        { error: error.reason, message: error.message },
        { status: error.status },
      );
    }
    throw error;
  }
}
