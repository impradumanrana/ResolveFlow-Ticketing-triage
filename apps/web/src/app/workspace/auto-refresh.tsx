"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

/**
 * Keeps the queue current without a page reload.
 *
 * Polling, not a push channel. A support queue changes every few minutes, and
 * a server-sent stream per agent is a connection to operate, scale, and debug
 * for a freshness nobody can perceive. Genuine push is worth revisiting only
 * if the pilot shows agents waiting on it.
 *
 * Refreshing under someone mid-task is worse than stale data, so it pauses
 * while the tab is hidden, while anything is selected, and on request.
 */
export function AutoRefresh({ intervalSeconds = 45 }: { intervalSeconds?: number }) {
  const router = useRouter();
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) {
      return;
    }
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") {
        return;
      }
      if (document.querySelector('input[type="checkbox"][name="ticketId"]:checked')) {
        return;
      }
      router.refresh();
    }, intervalSeconds * 1000);

    return () => window.clearInterval(timer);
  }, [router, intervalSeconds, paused]);

  return (
    <button
      className="ws-refresh"
      type="button"
      aria-pressed={paused}
      onClick={() => setPaused((value) => !value)}
    >
      {paused ? "Updates paused" : `Updating every ${intervalSeconds}s`}
    </button>
  );
}
