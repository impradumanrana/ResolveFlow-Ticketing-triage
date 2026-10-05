import type { Metadata } from "next";

import { signIn } from "@/lib/auth/config";

export const metadata: Metadata = { title: "Sign in" };

// Rendered per request so the C13 content-security-policy nonce can be
// stamped onto the framework's bootstrap scripts. A prerendered page has no
// nonce, and `strict-dynamic` makes `'self'` inoperative, so a static copy of
// this page would load no JavaScript at all.
export const dynamic = "force-dynamic";

export default function SignInPage() {
  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="signin-heading">
        <p className="auth-eyebrow">ResolveFlow</p>
        <h1 id="signin-heading">Sign in to your support workspace</h1>
        <p className="auth-lede">
          Access is limited to approved company domains and people who have been
          invited. A matching email domain on its own does not grant access.
        </p>

        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: "/workspace" });
          }}
        >
          <button className="auth-button" type="submit">
            Continue with Google Workspace
          </button>
        </form>

        <p className="auth-note">
          ResolveFlow is in Observe Mode. It never sends a message to a customer.
        </p>
      </section>
    </main>
  );
}
