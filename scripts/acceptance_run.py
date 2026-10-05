"""Pilot acceptance run (C14).

C11 built the Quality Check: ten labelled conversations against eleven gates.
That measures whether the pipeline is wired correctly and whether it stays
within its safety and cost envelope. It does **not** measure how accurately a
real model classifies the client's mail, for a reason worth being blunt about:
offline, the provider is deterministic and keyword-driven, and the ten cases
are the ones it was built against, so its accuracy is 1.0 by construction.

This script adds the half that needs no ground truth. It runs the forty-five
realistic conversations in `app/fixtures/realistic_ticket_batch_45.csv` - which
carry no labels, so nothing here claims accuracy - and measures the invariants
that must hold whatever the model answers:

* nothing is ever auto-resolved without validated grounding and a citation;
* a conversation that trips a guardrail is never auto-resolved;
* every citation exists in the corpus that was searched;
* every run records a route and a trace;
* the same input produces the same route twice (a pilot nobody can reproduce
  is not evidence);
* the latency distribution, and the cost per run.

It also reports the **route mix**, which is the number an operations manager
actually needs: how much of the inbox would reach a person.

Cost at volume is a projection from the approved price table, not a
measurement. No paid model is called: the run is offline and refuses to
pretend otherwise.

    python -m scripts.acceptance_run                # table on stdout
    python -m scripts.acceptance_run --json out.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "app" / "fixtures" / "realistic_ticket_batch_45.csv"

# Codes that mean a person must look. Mirrors the C09 guardrail vocabulary;
# a conversation carrying one of these may never be auto-resolved.
RISK_CODES = frozenset(
    {
        "THREAT_DETECTED",
        "SECURITY_RISK",
        "ANGRY_CUSTOMER",
        "PAYMENT_DISPUTE",
        "HIGH_URGENCY",
        "LEGAL_RISK",
    }
)

# What the pilot is asked to sustain. Agreed with the client before the run,
# and changed only by changing this file in a reviewed commit.
INVARIANTS: dict[str, str] = {
    "auto_resolve_always_grounded": (
        "Every auto-resolved conversation has validated grounding and at least one citation"
    ),
    "auto_resolve_never_risky": "No conversation that tripped a guardrail was auto-resolved",
    "citations_exist": "Every citation refers to an article in the corpus that was searched",
    "every_run_recorded": "Every conversation produced a route and a trace",
    "routes_are_reproducible": "A second pass produced the same route for every conversation",
    "no_send_path_exercised": (
        "No trace step names an outbound operation (checked against step and tool names, "
        "never against the customer's text)"
    ),
}


def load_corpus() -> list[dict[str, str]]:
    with CORPUS.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"{CORPUS} is empty")
    return rows


def run_once(pipeline: Any, organization: str, rows: list[dict[str, str]]) -> list[Any]:
    from app.triage.records import Conversation

    outcomes = []
    for row in rows:
        outcomes.append(
            pipeline.triage(
                Conversation(
                    organization_id=organization,
                    ticket_id=row["ticket_id"],
                    subject=row["subject"],
                    body=row["body"],
                    customer_id=row.get("customer_id") or None,
                    from_address=f"{(row.get('customer_id') or 'customer').lower()}@example.net",
                    mailbox_address="support@acme.example",
                )
            )
        )
    return outcomes


def corpus_article_ids() -> set[str]:
    fixture = ROOT / "app" / "fixtures" / "quality" / "knowledge_fixture.json"
    articles = json.loads(fixture.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for article in articles:
        for key in ("article_id", "id", "reference"):
            if key in article:
                ids.add(str(article[key]))
    return ids


# Identifiers that would name an outbound operation. Matched against step and
# tool *names* only - never against free text.
#
# The first version of this check searched the whole serialized step for
# "send", and failed on two conversations because a customer had written
# "please send me another link": the knowledge-search step carries the query.
# Scanning prose for a capability finds the customer's words, not the
# product's behaviour. The same mistake was made, and recorded, in C12.
# Verbs that name an outbound operation, matched against the *words* of an
# identifier. A word-boundary regex is no use here: `\bsend\b` does not match
# `send_reply` (an underscore is a word character) or `sendMessage` (camelCase
# has no boundary) - which are precisely the shapes a step would have. So the
# identifier is split into words first.
SEND_VERBS = frozenset(
    {
        "send",
        "sends",
        "sending",
        "resend",
        "deliver",
        "delivers",
        "dispatch",
        "transmit",
        "forward",
    }
)
# Deliberately absent: "reply". A draft reply is the product working as
# intended, and a step named `draft_reply` must not be flagged.

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def identifier_words(identifier: str) -> set[str]:
    """`sendMessage` and `send_reply` both yield `{"send", ...}`.

    `sender_address` yields `{"sender", "address"}` and so does not match: a
    field naming who wrote to us is not an operation that writes back.
    """
    return {part.casefold() for part in _WORD_SPLIT.split(identifier) if part}


def step_identifiers(outcome: Any) -> set[str]:
    """Every name a trace step goes by, with no free text included."""
    names: set[str] = set()
    for step in outcome.trace:
        if not isinstance(step, dict):
            continue
        for key in ("node", "status"):
            value = step.get(key)
            if isinstance(value, str):
                names.add(value)
        data = step.get("data")
        if isinstance(data, dict):
            for key in ("tool_name", "operation", "endpoint", "action"):
                value = data.get(key)
                if isinstance(value, str):
                    names.add(value)
    return names


def send_shaped_steps(outcome: Any) -> set[str]:
    return {name for name in step_identifiers(outcome) if identifier_words(name) & SEND_VERBS}


def evaluate(outcomes: list[Any], second_pass: list[Any], known: set[str]) -> dict[str, Any]:
    failures: dict[str, list[str]] = {name: [] for name in INVARIANTS}

    for outcome, again in zip(outcomes, second_pass, strict=True):
        auto = outcome.route == "AUTO_RESOLVE"
        risky = bool(set(outcome.rule_codes) & RISK_CODES)

        if auto and not (outcome.grounding_validated and outcome.citations):
            failures["auto_resolve_always_grounded"].append(outcome.correlation_id)
        if auto and risky:
            failures["auto_resolve_never_risky"].append(outcome.correlation_id)
        unknown = {c for c in outcome.citations if known and c not in known}
        if unknown:
            failures["citations_exist"].append(f"{outcome.correlation_id}: {sorted(unknown)}")
        if not outcome.route or not outcome.trace:
            failures["every_run_recorded"].append(outcome.correlation_id)
        if outcome.route != again.route:
            failures["routes_are_reproducible"].append(
                f"{outcome.correlation_id}: {outcome.route} then {again.route}"
            )
        if send_shaped_steps(outcome):
            failures["no_send_path_exercised"].append(
                f"{outcome.correlation_id}: {sorted(send_shaped_steps(outcome))}"
            )

    latencies = sorted(o.processing_ms for o in outcomes)
    costs = [o.cost_micro for o in outcomes]
    routes: dict[str, int] = {}
    for outcome in outcomes:
        routes[outcome.route] = routes.get(outcome.route, 0) + 1

    def percentile(values: list[int], fraction: float) -> int:
        if not values:
            return 0
        index = min(len(values) - 1, int(round(fraction * (len(values) - 1))))
        return values[index]

    return {
        "cases": len(outcomes),
        "invariants": {
            name: {"description": INVARIANTS[name], "passed": not found, "failures": found}
            for name, found in failures.items()
        },
        "routes": routes,
        "human_share": round(
            sum(count for route, count in routes.items() if route != "AUTO_RESOLVE")
            / max(1, len(outcomes)),
            3,
        ),
        "guardrail_share": round(
            sum(1 for o in outcomes if set(o.rule_codes) & RISK_CODES) / max(1, len(outcomes)), 3
        ),
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": latencies[-1] if latencies else 0,
            "mean": round(statistics.fmean(latencies), 1) if latencies else 0,
        },
        "cost_micro_units": {
            "mean_per_run": round(statistics.fmean(costs), 1) if costs else 0,
            "total": sum(costs),
            "note": (
                "Offline simulation. Real cost depends on the client's provider and prices; "
                "this measures that cost is accounted for per run, not what it will be."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="write the full report here")
    arguments = parser.parse_args()

    from scripts.quality_check import offline_parts

    pipeline, knowledge, organization, fingerprint, article_count = offline_parts()
    rows = load_corpus()
    try:
        first = run_once(pipeline, organization, rows)
        second = run_once(pipeline, organization, rows)
    finally:
        close = getattr(knowledge, "close", None)
        if close:
            close()

    report = evaluate(first, second, corpus_article_ids())
    report["corpus"] = {"fingerprint": fingerprint, "articles": article_count}
    report["source"] = CORPUS.relative_to(ROOT).as_posix()
    report["provider"] = "offline deterministic simulation; no paid model was called"
    report["measures_accuracy"] = False
    report["accuracy_note"] = (
        "This corpus carries no labels, so no accuracy is claimed. Accuracy against the "
        "client's own convention requires the client's labelled cases and their approved "
        "provider; see docs/UAT_CRITERIA.md."
    )

    print(f"\npilot acceptance run - {report['cases']} conversations, {CORPUS.name}")
    print(f"corpus {fingerprint} ({article_count} articles), offline provider\n")
    print(f"{'invariant':38} verdict  failures")
    for name, result in report["invariants"].items():
        verdict = "pass" if result["passed"] else "FAIL"
        print(f"{name:38} {verdict:7}  {len(result['failures'])}")
        for failure in result["failures"][:3]:
            print(f"{'':38}          {failure}")

    print(f"\nroute mix: {report['routes']}")
    print(f"reaches a person: {report['human_share'] * 100:.1f}%")
    print(f"tripped a guardrail: {report['guardrail_share'] * 100:.1f}%")
    latency = report["latency_ms"]
    print(f"latency ms: p50 {latency['p50']}  p95 {latency['p95']}  max {latency['max']}")
    print(f"cost micro-units per run: {report['cost_micro_units']['mean_per_run']}")

    failed = [name for name, result in report["invariants"].items() if not result["passed"]]
    verdict = "PASS" if not failed else "FAIL"
    print(f"\nVerdict: {verdict}")
    if report["measures_accuracy"] is False:
        print("No accuracy is claimed: this corpus is unlabelled. See the note in --json.")

    if arguments.json:
        arguments.json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"report written to {arguments.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
