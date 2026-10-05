import type { ReactNode } from "react";
import Link from "next/link";

import { signOut } from "@/lib/auth/config";
import { requirePageAccess } from "@/lib/auth/session";
import { can } from "@/lib/authz/authorize";

/**
 * The workspace shell.
 *
 * The skip link is the first focusable element on every page: an agent working
 * by keyboard should not tab through navigation to reach the queue they came
 * for.
 */
export default async function WorkspaceLayout({ children }: { children: ReactNode }) {
  const context = await requirePageAccess("workspace.view");

  return (
    <div className="ws">
      <a className="ws-skip" href="#main">
        Skip to main content
      </a>

      <header className="ws-header">
        <div className="ws-brand">
          <span className="ws-mark" aria-hidden="true" />
          <span className="ws-brandname">ResolveFlow</span>
          <span className="ws-mode" title="No message is ever sent to a customer">
            Observe Mode
          </span>
        </div>

        <nav className="ws-nav" aria-label="Workspace">
          <Link href="/workspace">Overview</Link>
          <Link href="/workspace/inbox">Inbox</Link>
          {can(context, "quality.view") ? <Link href="/workspace/quality">Quality</Link> : null}
          {can(context, "ai_settings.view") ? <Link href="/workspace/settings/ai">AI provider</Link> : null}
        </nav>

        <div className="ws-account">
          <span className="ws-email" title={context.email}>
            {context.email}
          </span>
          <span className="ws-role">{context.role.replaceAll("_", " ").toLowerCase()}</span>
          <form
            action={async () => {
              "use server";
              await signOut({ redirectTo: "/signin" });
            }}
          >
            <button className="ws-signout" type="submit">
              Sign out
            </button>
          </form>
        </div>
      </header>

      <main id="main" className="ws-main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
