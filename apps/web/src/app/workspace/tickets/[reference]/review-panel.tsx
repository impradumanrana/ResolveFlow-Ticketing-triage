"use client";

import { useActionState } from "react";

import {
  DECISIONS,
  previewSummary,
  type ActionResult,
  type DecisionName,
  type DraftPreviewView,
} from "@/lib/review/view";

import { decide } from "./review-actions";

interface Option {
  readonly id: string;
  readonly label: string;
}

interface Props {
  readonly ticketId: string;
  readonly reference: number;
  readonly version: number;
  readonly nonce: string;
  readonly allowed: readonly DecisionName[];
  readonly draftBody: string | null;
  readonly preview: DraftPreviewView | null;
  readonly assignees: readonly Option[];
  readonly queues: readonly Option[];
  readonly departments: readonly Option[];
}

function Result({ result }: { result: ActionResult | null }) {
  if (!result) {
    return <p className="ws-note" role="status" aria-live="polite" />;
  }
  return (
    <p className={result.ok ? "ws-grounded" : "ws-ungrounded"} role="status" aria-live="polite">
      {result.message}
      {result.reload ? " " : null}
      {result.reload ? <a href="">Reload this conversation</a> : null}
    </p>
  );
}

/**
 * The fields every decision carries.
 *
 * `decision` is omitted where a form offers more than one, because a hidden
 * input and a submit button of the same name both post a value and the first
 * one wins - which once made "Approve" record an edit. The key is derived on
 * the server from the decision actually pressed and this nonce, so pressing
 * Save and then Approve are two decisions rather than one replay.
 */
function hidden(props: Props, decision: DecisionName | null) {
  return (
    <>
      {decision ? <input type="hidden" name="decision" value={decision} /> : null}
      <input type="hidden" name="ticketId" value={props.ticketId} />
      <input type="hidden" name="reference" value={props.reference} />
      <input type="hidden" name="expectedVersion" value={props.version} />
      <input type="hidden" name="nonce" value={props.nonce} />
    </>
  );
}

export function ReviewPanel(props: Props) {
  const [result, submit, pending] = useActionState<ActionResult | null, FormData>(decide, null);
  const allowed = new Set(props.allowed);
  const spec = (decision: DecisionName) => DECISIONS.find((item) => item.decision === decision)!;

  return (
    <section aria-labelledby="review-heading" className="ws-panel">
      <h2 id="review-heading">Your decision</h2>
      <p className="ws-note">{previewSummary(props.preview)}</p>

      {props.preview ? (
        <details className="ws-preview">
          <summary>What approving would create</summary>
          <dl className="ws-facts">
            <dt>From</dt>
            <dd>{props.preview.from_address}</dd>
            <dt>To</dt>
            <dd>{props.preview.to.join(", ")}</dd>
            <dt>Subject</dt>
            <dd>{props.preview.subject}</dd>
          </dl>
          <blockquote className="ws-quote">{props.preview.body}</blockquote>
          {props.preview.notes.map((note) => (
            <p className="ws-note" key={note}>
              {note}
            </p>
          ))}
          <p className="ws-note">{props.preview.effect}</p>
        </details>
      ) : null}

      <Result result={result} />

      {allowed.has("EDIT") || allowed.has("APPROVE") ? (
        <form action={submit} className="ws-field ws-reviewform">
          {hidden(props, allowed.has("EDIT") && allowed.has("APPROVE") ? null : allowed.has("EDIT") ? "EDIT" : "APPROVE")}
          <label htmlFor="review-body">Reply</label>
          <textarea
            id="review-body"
            name="body"
            rows={8}
            defaultValue={props.draftBody ?? ""}
            maxLength={20000}
            disabled={pending}
            aria-describedby="review-body-help"
          />
          <p id="review-body-help" className="ws-note">
            {props.draftBody
              ? "This answer was checked against its cited sources. Edit it if it needs changing."
              : "There is no verified answer for this conversation. Anything you write here is your own, recorded as yours."}
          </p>
          <div className="ws-inlineform">
            {allowed.has("EDIT") ? (
              <button
                className="ws-button"
                type="submit"
                name="decision"
                value="EDIT"
                disabled={pending}
              >
                {pending ? "Saving…" : spec("EDIT").label}
              </button>
            ) : null}
            {allowed.has("APPROVE") ? (
              <button
                className="ws-button"
                type="submit"
                name="decision"
                value="APPROVE"
                disabled={pending}
              >
                {pending ? "Approving…" : spec("APPROVE").label}
              </button>
            ) : null}
          </div>
          <p className="ws-note">{spec("APPROVE").effect}</p>
        </form>
      ) : null}

      {allowed.has("REJECT") ? (
        <form action={submit} className="ws-field ws-reviewform">
          {hidden(props, "REJECT")}
          <label htmlFor="reject-reason">Why this is not being answered automatically</label>
          <input id="reject-reason" name="reason" maxLength={2000} required disabled={pending} />
          <div>
            <button className="ws-button" type="submit" disabled={pending}>
              {pending ? "Recording…" : spec("REJECT").label}
            </button>
          </div>
          <p className="ws-note">{spec("REJECT").effect}</p>
        </form>
      ) : null}

      {allowed.has("REROUTE") ? (
        <form action={submit} className="ws-field ws-reviewform">
          {hidden(props, "REROUTE")}
          <label htmlFor="reroute-queue">Move to queue</label>
          <select id="reroute-queue" name="queueId" defaultValue="" disabled={pending}>
            <option value="">Leave as it is</option>
            {props.queues.map((queue) => (
              <option key={queue.id} value={queue.id}>
                {queue.label}
              </option>
            ))}
          </select>
          <label htmlFor="reroute-department">Move to department</label>
          <select id="reroute-department" name="departmentId" defaultValue="" disabled={pending}>
            <option value="">Leave as it is</option>
            {props.departments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.label}
              </option>
            ))}
          </select>
          <label htmlFor="reroute-reason">Why it is moving</label>
          <input id="reroute-reason" name="reason" maxLength={2000} required disabled={pending} />
          <div>
            <button className="ws-button" type="submit" disabled={pending}>
              {pending ? "Moving…" : spec("REROUTE").label}
            </button>
          </div>
        </form>
      ) : null}

      {allowed.has("ASSIGN") ? (
        <form action={submit} className="ws-field ws-reviewform">
          {hidden(props, "ASSIGN")}
          <label htmlFor="assign-to">Assign to</label>
          <select id="assign-to" name="assigneeMembershipId" defaultValue="" disabled={pending}>
            <option value="">Choose a person</option>
            {props.assignees.map((person) => (
              <option key={person.id} value={person.id}>
                {person.label}
              </option>
            ))}
          </select>
          <div>
            <button className="ws-button" type="submit" disabled={pending}>
              {pending ? "Assigning…" : spec("ASSIGN").label}
            </button>
          </div>
        </form>
      ) : null}

      {allowed.has("RESOLVE") ? (
        <form action={submit} className="ws-inlineform">
          {hidden(props, "RESOLVE")}
          <button className="ws-button" type="submit" disabled={pending}>
            {pending ? "Closing…" : spec("RESOLVE").label}
          </button>
          <span className="ws-note">{spec("RESOLVE").effect}</span>
        </form>
      ) : null}
    </section>
  );
}
