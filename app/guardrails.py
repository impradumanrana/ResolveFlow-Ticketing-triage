from __future__ import annotations

import re
from typing import Any


def contains_any(text: str, phrases: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) for phrase in phrases)


def evaluate_guardrails(facts: dict[str, Any]) -> dict[str, Any]:
    rule_codes: list[str] = []
    text = (facts.get("content") or "").lower()
    urgency = str(facts.get("urgency") or "low").lower()

    if urgency == "critical":
        rule_codes.append("CRITICAL_URGENCY")
    elif urgency == "high":
        rule_codes.append("HIGH_URGENCY")

    if contains_any(text, [
        "angry",
        "furious",
        "outraged",
        "threaten",
        "threatening",
        "i will sue",
        "lawsuit",
        "legal",
        "regulator",
        "report you",
        "chargeback",
        "i am furious",
        "leave unless",
    ]):
        rule_codes.append("ANGRY_CUSTOMER")

    if contains_any(text, [
        "threat",
        "i will sue",
        "kill",
        "legal",
        "lawsuit",
        "regulator",
        "report you",
        "ignore previous instructions",
        "admin password",
        "secret admin password",
        "tell me the secret",
    ]):
        rule_codes.append("THREAT_DETECTED")

    if (
        "password" in text and ("hacked" in text or "takeover" in text or "unauthorized" in text or "security breach" in text)
    ) or "account takeover" in text or "unauthorized access" in text or "security review" in text or "someone logged into my account" in text or "did not request" in text and "password reset" in text:
        rule_codes.append("SECURITY_RISK")

    if contains_any(text, [
        "charged twice",
        "double charge",
        "payment failed",
        "payment failure",
        "declined",
        "card was declined",
        "failed charge",
        "payment issue",
        "card is failing",
        "payment failure",
    ]):
        rule_codes.append("PAYMENT_FAILURE")

    if contains_any(text, [
        "refund",
        "legal",
        "sue",
        "lawsuit",
        "chargeback",
        "credit",
        "report you",
    ]):
        rule_codes.append("REFUND_OR_LEGAL")

    if (
        ("invoice" in text and "missing" in text)
        or ("order" in text and "missing" in text)
        or ("invoice" in text and "do not" in text and "have" in text)
        or ("order" in text and "do not" in text and "have" in text)
        or ("billing question" in text and "invoice" not in text and "order" not in text and "payment" not in text)
        or ("order confirmation" in text and ("do not remember" in text or "forgot" in text or "need" in text and "email" in text))
        or ("email address" in text and "order" in text and "remember" in text)
        or ("remember the email" in text and "order" in text)
    ):
        rule_codes.append("MISSING_INFORMATION")

    if any(token in text for token in ["missing email", "need email", "no invoice", "no order", "i do not know my order", "i do not have the order"]):
        rule_codes.append("MISSING_INFORMATION")
    if any(token in text for token in ["do not remember", "forgot"]) and any(
        context in text for context in ["order", "invoice", "purchase email", "billing email"]
    ):
        rule_codes.append("MISSING_INFORMATION")

    if not text.strip():
        rule_codes.append("MISSING_INFORMATION")

    seen: set[str] = set()
    deduped = []
    for code in rule_codes:
        if code not in seen:
            deduped.append(code)
            seen.add(code)

    return {
        "rule_codes": deduped,
        "blocking": any(
            code in deduped for code in [
                "HIGH_URGENCY",
                "CRITICAL_URGENCY",
                "ANGRY_CUSTOMER",
                "THREAT_DETECTED",
                "SECURITY_RISK",
                "PAYMENT_FAILURE",
                "REFUND_OR_LEGAL",
                "MISSING_INFORMATION",
            ]
        ),
    }
