"""C11 gate: the demonstration visibly shows the whole chain.

The gate asks for a demo that shows request -> MCP call -> knowledge response
-> grounded answer -> validator. This runs it and checks that each of those is
actually printed, with real values rather than a description of them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_demo(
    *arguments: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    import os

    environment = {**os.environ, "RESOLVEFLOW_TEST_MODE": "1", **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "triage_demo.py"), *arguments],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=environment,
        timeout=180,
    )


@pytest.fixture(scope="module")
def demo() -> subprocess.CompletedProcess[str]:
    result = run_demo()
    assert result.returncode == 0, result.stderr[-2000:]
    return result


def test_the_demonstration_shows_each_boundary_in_order(demo):
    stages = [
        "1. REQUEST",
        "2. MODEL CALL",
        "3. GUARDRAILS",
        "4. MCP CALL",
        "5. KNOWLEDGE RESPONSE",
        "6. DECISION",
        "7. GROUNDED ANSWER",
        "8. VALIDATOR",
        "9. RECORDED",
    ]
    positions = [demo.stdout.index(stage) for stage in stages]
    assert positions == sorted(positions), "the stages are out of order"


def test_the_mcp_call_and_its_response_are_shown_with_real_values(demo):
    assert "transport: stdio" in demo.stdout
    assert "search_knowledge_base" in demo.stdout
    assert '"query"' in demo.stdout and '"top_k"' in demo.stdout
    # The retrieved passage, its score, and its text.
    assert "KB-001" in demo.stdout
    assert "Open the sign-in page and choose Forgot password" in demo.stdout


def test_the_answer_is_grounded_and_the_validator_agrees(demo):
    answer = demo.stdout.split("7. GROUNDED ANSWER")[1].split("8. VALIDATOR")[0]
    assert "[KB-001]" in answer, "the draft must carry its citation"
    validator = demo.stdout.split("8. VALIDATOR")[1]
    assert '"valid": true' in validator
    assert '"verified_claims": 1' in validator


def test_the_run_is_reported_with_its_cost_and_route(demo):
    recorded = demo.stdout.split("9. RECORDED")[1]
    assert "route:       AUTO_RESOLVE" in recorded
    assert "correlation: triage-" in recorded
    assert "micro-units" in recorded
    assert "Nothing was sent to the customer" in demo.stdout


def test_a_risky_conversation_is_shown_going_to_a_person():
    result = run_demo(
        "--subject",
        "Refund or I take this further",
        "--body",
        "I want a refund and I will take legal action if I do not get one.",
    )
    assert result.returncode == 0
    assert "THREAT_DETECTED" in result.stdout
    assert "final route after client rules: ESCALATE" in result.stdout
    # No customer answer is produced: the draft slot holds the review note.
    answer = result.stdout.split("7. GROUNDED ANSWER")[1].split("8. VALIDATOR")[0]
    assert "Human review required" in answer
    assert "Triggered controls" in answer
    assert "route:       ESCALATE" in result.stdout


def test_a_live_demonstration_refuses_without_authorization():
    result = run_demo("--live", "--database-url", "postgresql://x/y", "--organization", "org")
    assert result.returncode == 2
    assert "Refusing" in result.stderr and "paid model" in result.stderr


def test_the_quality_check_script_reports_every_gate_and_its_verdict():
    import os

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "quality_check.py"), "--json"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "RESOLVEFLOW_TEST_MODE": "1"},
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout[result.stdout.index("{") :])
    assert payload["passed"] is True
    assert {gate["gate"] for gate in payload["gates"]} == {
        "classification",
        "retrieval",
        "groundedness",
        "safety",
        "latency",
        "cost",
    }
    assert payload["dataset_version"] and payload["threshold_version"]
    assert payload["knowledge_fingerprint"].startswith("fixture-")


def test_the_quality_check_refuses_a_live_run_without_authorization():
    import os

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "quality_check.py"),
            "--live",
            "--database-url",
            "postgresql://x/y",
            "--organization",
            "org",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "RESOLVEFLOW_TEST_MODE": "1"},
        timeout=120,
    )
    assert result.returncode == 2
    assert "Refusing" in result.stderr
