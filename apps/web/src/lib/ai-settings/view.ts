/**
 * Presentation rules for the AI provider settings page.
 *
 * Framework-free so the rules that decide what an operator reads - status,
 * spend, budget warnings, result messages - are tested directly. Nothing here
 * ever receives a key: the internal API returns masked metadata only.
 */

export type ProviderStatus = "HEALTHY" | "FAILING" | "UNVERIFIED";

export interface AiProviderView {
  readonly provider: string;
  readonly status: ProviderStatus;
  readonly credential_hint: string;
  readonly region: string | null;
  readonly models: Readonly<Record<string, string>>;
  readonly fallback: readonly string[];
  readonly approved_models: readonly string[];
  readonly budget_minor_units: number | null;
  readonly currency: string;
  readonly spent_minor_units: number;
  readonly reserved_minor_units: number;
  readonly last_verified_at: string | null;
  readonly last_failure_code: string | null;
  readonly last_failure_message: string | null;
  readonly last_failure_at: string | null;
}

export interface AiFailureCount {
  readonly code: string;
  readonly count: number;
  readonly message: string | null;
}

export interface AiSettingsView {
  readonly providers: readonly AiProviderView[];
  readonly recent_failures: readonly AiFailureCount[];
  readonly fallback_uses: number;
  readonly cost_note: string;
}

export interface VerificationView {
  readonly provider: string;
  readonly ok: boolean;
  readonly stored: boolean;
  readonly code: string | null;
  readonly message: string | null;
  readonly checked_models: readonly string[];
}

export const PROVIDER_PATTERN = /^[a-z][a-z0-9-]{1,31}$/;
export const KEY_FIELD = "apiKey";
/** Longer than any provider key; a bound on what the form will forward. */
export const MAX_KEY_INPUT = 1024;
export const BUDGET_WARNING_RATIO = 0.8;

const STATUS_LABELS: Readonly<Record<ProviderStatus, string>> = {
  HEALTHY: "Working",
  FAILING: "Failing",
  UNVERIFIED: "Not yet verified",
};

/** Existing `ws-badge--*` classes, each backed by a contrast-checked token pair. */
const STATUS_TONES: Readonly<Record<ProviderStatus, string>> = {
  HEALTHY: "resolved",
  FAILING: "failed",
  UNVERIFIED: "awaiting-approval",
};

export function statusLabel(status: ProviderStatus): string {
  return STATUS_LABELS[status] ?? "Unknown";
}

export function statusTone(status: ProviderStatus): string {
  return STATUS_TONES[status] ?? "open";
}

function fractionDigits(currency: string): number {
  try {
    return (
      new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions()
        .maximumFractionDigits ?? 2
    );
  } catch {
    return 2;
  }
}

/**
 * Minor units to a currency string. Sub-unit spend is shown as "less than"
 * rather than rounded to zero: a meter that reads 0.00 after real calls looks
 * broken.
 */
export function formatMoney(minorUnits: number, currency: string): string {
  const digits = fractionDigits(currency);
  const major = minorUnits / 10 ** digits;
  let formatter: Intl.NumberFormat;
  try {
    formatter = new Intl.NumberFormat("en", {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  } catch {
    return `${major.toFixed(2)} ${currency}`;
  }
  if (minorUnits > 0 && minorUnits < 1) {
    return `less than ${formatter.format(1 / 10 ** digits)}`;
  }
  return formatter.format(major);
}

export type BudgetState = "UNSET" | "OK" | "NEAR" | "EXHAUSTED";

export interface BudgetUsage {
  readonly state: BudgetState;
  /** Committed share of the budget, 0-100, counting in-flight reservations. */
  readonly percent: number | null;
  readonly summary: string;
}

export function budgetUsage(provider: AiProviderView): BudgetUsage {
  const budget = provider.budget_minor_units;
  if (budget === null) {
    return {
      state: "UNSET",
      percent: null,
      summary: "No monthly budget is set, so AI calls are paused until one is.",
    };
  }
  const committed = provider.spent_minor_units + provider.reserved_minor_units;
  const ratio = budget === 0 ? 1 : committed / budget;
  const percent = Math.min(100, Math.round(ratio * 100));
  const spent = formatMoney(provider.spent_minor_units, provider.currency);
  const limit = formatMoney(budget, provider.currency);
  const state: BudgetState = ratio >= 1 ? "EXHAUSTED" : ratio >= BUDGET_WARNING_RATIO ? "NEAR" : "OK";
  const suffix =
    state === "EXHAUSTED"
      ? " The budget is used up; AI calls are paused until next month or until it is raised."
      : state === "NEAR"
        ? " Nearly used up."
        : "";
  return { state, percent, summary: `${spent} of ${limit} estimated this month.${suffix}` };
}

export function verificationMessage(result: VerificationView | null, status: number | null): string {
  if (status === 403) {
    return "You do not have permission to change AI settings.";
  }
  if (status === 404) {
    return "That provider is not configured.";
  }
  if (result === null) {
    return "The AI service could not be reached. Nothing was changed.";
  }
  if (result.ok && result.stored) {
    return `The new key works for ${result.checked_models.length} model${
      result.checked_models.length === 1 ? "" : "s"
    } and has been stored. The previous key is no longer used.`;
  }
  if (result.ok) {
    return `The stored key works for ${result.checked_models.length} model${
      result.checked_models.length === 1 ? "" : "s"
    }.`;
  }
  const reason = result.message ?? "The check failed.";
  return result.stored ? reason : `${reason} Nothing was changed.`;
}

/** Validate what the key form submitted, without ever echoing it. */
export function readKeyInput(value: unknown): { ok: true; key: string } | { ok: false; message: string } {
  if (typeof value !== "string" || value.trim() === "") {
    return { ok: false, message: "Paste the new API key first." };
  }
  if (value.length > MAX_KEY_INPUT) {
    return { ok: false, message: "That does not look like an API key. Nothing was changed." };
  }
  return { ok: true, key: value };
}

/** One line per problem: the gateway's own words where it has them. */
export function describeFailure(failure: AiFailureCount): string {
  const times = `${failure.count} time${failure.count === 1 ? "" : "s"}`;
  const words = failure.message ?? failure.code.replaceAll("_", " ").toLowerCase();
  return `${words} (${times})`;
}

export function describeFallbacks(uses: number): string | null {
  if (uses <= 0) {
    return null;
  }
  return `A fallback model answered ${uses} time${uses === 1 ? "" : "s"} because the first choice was unavailable.`;
}
