"use client";

import { useEffect } from "react";

/**
 * Keyboard navigation for the conversation list.
 *
 * Arrow keys and j/k move between rows, matching what agents expect from a
 * mail client. It is an accelerator, not the only way in: every row is a real
 * link and reachable by Tab without this component. The handler stays out of
 * the way while someone is typing in a field or using a modifier.
 */
export function ListKeyboard({ selector }: { selector: string }) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || target?.isContentEditable) {
        return;
      }

      const forward = event.key === "ArrowDown" || event.key === "j";
      const backward = event.key === "ArrowUp" || event.key === "k";
      if (!forward && !backward) {
        return;
      }

      const rows = Array.from(document.querySelectorAll<HTMLElement>(selector));
      if (rows.length === 0) {
        return;
      }
      const current = rows.findIndex((row) => row.contains(document.activeElement));
      const next = current === -1 ? 0 : Math.min(Math.max(current + (forward ? 1 : -1), 0), rows.length - 1);

      event.preventDefault();
      rows[next]?.focus();
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [selector]);

  return null;
}
