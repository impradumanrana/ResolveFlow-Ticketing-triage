"use client";

import { useActionState } from "react";

import { assignSelectedTickets, type AssignmentResult } from "../actions";

/**
 * Bulk assignment for the rows selected above.
 *
 * The result is announced rather than silently applied: assigning twenty
 * conversations to the wrong person is easy to do and hard to notice, so the
 * outcome says how many actually changed.
 */
export function BulkAssign({ assignees }: { assignees: readonly { id: string; label: string }[] }) {
  const [result, submit, pending] = useActionState<AssignmentResult | null, FormData>(
    assignSelectedTickets,
    null,
  );

  return (
    <form id="bulk-assign" action={submit} className="ws-bulk" aria-label="Assign selected conversations">
      <label htmlFor="bulk-assignee">Assign selected to</label>
      <select id="bulk-assignee" name="assignee" defaultValue="unassigned" disabled={pending}>
        <option value="unassigned">Nobody (unassign)</option>
        {assignees.map((assignee) => (
          <option key={assignee.id} value={assignee.id}>
            {assignee.label}
          </option>
        ))}
      </select>
      <button className="ws-button" type="submit" disabled={pending}>
        {pending ? "Assigning…" : "Assign"}
      </button>
      <p className="ws-bulkresult" role="status" aria-live="polite">
        {result?.message ?? ""}
      </p>
    </form>
  );
}
