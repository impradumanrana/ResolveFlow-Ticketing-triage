"use server";

/**
 * Server actions for AI provider settings.
 *
 * The key travels browser -> this action -> the private API, in request
 * bodies only. It is never placed in a URL, a redirect, a cookie, a returned
 * value, or a log line, and the result returned to the browser contains only
 * a message and a success flag.
 */

import { revalidatePath } from "next/cache";

import {
  KEY_FIELD,
  PROVIDER_PATTERN,
  readKeyInput,
  verificationMessage,
  type VerificationView,
} from "@/lib/ai-settings/view";
import { requireAccess } from "@/lib/auth/session";
import { callAiApi } from "@/lib/bff/ai-api";

export interface SettingsActionResult {
  readonly ok: boolean;
  readonly message: string;
}

function providerFrom(formData: FormData): string | null {
  const provider = String(formData.get("provider") ?? "");
  return PROVIDER_PATTERN.test(provider) ? provider : null;
}

export async function verifyProvider(
  _previous: SettingsActionResult | null,
  formData: FormData,
): Promise<SettingsActionResult> {
  const context = await requireAccess("ai_settings.manage");
  const provider = providerFrom(formData);
  if (!provider) {
    return { ok: false, message: "That provider is not configured." };
  }

  const result = await callAiApi<VerificationView>(context, {
    path: `/v1/ai/providers/${provider}/verify`,
    method: "POST",
  });
  revalidatePath("/workspace/settings/ai");
  return {
    ok: Boolean(result.data?.ok),
    message: verificationMessage(result.data, result.ok ? null : result.status),
  };
}

export async function replaceProviderKey(
  _previous: SettingsActionResult | null,
  formData: FormData,
): Promise<SettingsActionResult> {
  const context = await requireAccess("ai_settings.manage");
  const provider = providerFrom(formData);
  if (!provider) {
    return { ok: false, message: "That provider is not configured." };
  }
  const input = readKeyInput(formData.get(KEY_FIELD));
  if (!input.ok) {
    return { ok: false, message: input.message };
  }

  const result = await callAiApi<VerificationView>(context, {
    path: `/v1/ai/providers/${provider}/credential`,
    method: "PUT",
    body: { api_key: input.key },
  });
  revalidatePath("/workspace/settings/ai");
  return {
    ok: Boolean(result.data?.ok && result.data.stored),
    message: verificationMessage(result.data, result.ok ? null : result.status),
  };
}
