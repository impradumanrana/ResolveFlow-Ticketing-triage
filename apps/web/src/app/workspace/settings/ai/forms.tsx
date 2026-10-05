"use client";

import { useActionState } from "react";

import { KEY_FIELD, MAX_KEY_INPUT } from "@/lib/ai-settings/view";

import { replaceProviderKey, verifyProvider, type SettingsActionResult } from "./actions";

/**
 * Both forms are plain server-action forms: they work before JavaScript loads,
 * and React resets the key field after submission so the pasted key does not
 * linger in the page.
 */
export function VerifyForm({ provider }: { provider: string }) {
  const [result, submit, pending] = useActionState<SettingsActionResult | null, FormData>(
    verifyProvider,
    null,
  );
  return (
    <form action={submit} className="ws-inlineform">
      <input type="hidden" name="provider" value={provider} />
      <button className="ws-button" type="submit" disabled={pending}>
        {pending ? "Checking…" : "Check the stored key"}
      </button>
      <p className="ws-note" role="status" aria-live="polite">
        {result?.message ?? ""}
      </p>
    </form>
  );
}

export function ReplaceKeyForm({ provider }: { provider: string }) {
  const [result, submit, pending] = useActionState<SettingsActionResult | null, FormData>(
    replaceProviderKey,
    null,
  );
  const inputId = `new-key-${provider}`;
  const helpId = `${inputId}-help`;
  return (
    <form action={submit} className="ws-field ws-keyform" aria-label={`Replace the ${provider} API key`}>
      <input type="hidden" name="provider" value={provider} />
      <label htmlFor={inputId}>New API key</label>
      <input
        id={inputId}
        name={KEY_FIELD}
        type="password"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        maxLength={MAX_KEY_INPUT}
        required
        aria-describedby={helpId}
        disabled={pending}
      />
      <p id={helpId} className="ws-note">
        The key is checked with the provider first and stored only if it works. It is never shown
        again; only its last four characters are displayed.
      </p>
      <div>
        <button className="ws-button" type="submit" disabled={pending}>
          {pending ? "Checking…" : "Check and store key"}
        </button>
      </div>
      <p className="ws-note" role="status" aria-live="polite">
        {result?.message ?? ""}
      </p>
    </form>
  );
}
