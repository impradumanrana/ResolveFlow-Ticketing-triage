from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from openai import APIError

from app.config import OPENAI_API_KEY, OPENAI_MODEL
from app.models import Classification


class DeterministicProvider:
    def classify(self, ticket_text: str) -> dict[str, Any]:
        text = (ticket_text or "").lower()

        if any(phrase in text for phrase in [
            "account takeover",
            "unauthorized access",
            "security review",
            "my account was hacked",
            "someone logged into my account",
            "password reset email i did not request",
            "suspect someone is trying to hack my account",
            "i think my account was hacked",
            "security breach",
        ]):
            return {"category": "account", "urgency": "critical", "confidence": 0.96, "queue": "Security Ops"}

        if any(phrase in text for phrase in [
            "charged twice",
            "payment failed",
            "payment failure",
            "card was declined",
            "failed charge",
            "refund",
            "chargeback",
            "dispute this charge",
            "legal claim",
            "billing question",
            "invoice",
            "order confirmation",
            "receipt",
            "subscription",
            "cancel my subscription",
            "payment method",
            "billing cycle",
            "renewal",
        ]):
            return {"category": "billing", "urgency": "high" if any(phrase in text for phrase in ["charged twice", "payment failed", "card was declined", "refund", "chargeback", "legal claim"]) else "medium", "confidence": 0.89, "queue": "Billing Review"}

        if any(phrase in text for phrase in [
            "password reset",
            "reset link",
            "forgot password",
            "forgot username",
            "locked out",
            "locked account",
            "2fa",
            "authenticator",
            "login help",
            "cannot log in",
            "crash",
            "sync",
            "download",
            "dashboard",
            "permissions",
            "reinstall",
            "cache",
            "app is broken",
        ]):
            category = "account" if any(term in text for term in ["locked out", "locked account", "forgot username", "login help"]) else "technical"
            return {"category": category, "urgency": "high" if any(term in text for term in ["locked out", "locked account"]) else "low", "confidence": 0.92, "queue": "Account Recovery" if category == "account" else "Tier-1 Technical"}

        return {"category": "technical", "urgency": "medium", "confidence": 0.72, "queue": "General Support"}

    def health_check(self) -> bool:
        return True


class OpenAIProvider:
    def __init__(self) -> None:
        self.client = None
        self.model = OPENAI_MODEL
        if OPENAI_API_KEY:
            self.client = OpenAI(api_key=OPENAI_API_KEY)

    def classify(self, ticket_text: str) -> dict[str, Any]:
        if not self.client:
            return DeterministicProvider().classify(ticket_text)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a support triage classifier. Return only valid JSON with keys: category, urgency, confidence, queue.",
                    },
                    {"role": "user", "content": ticket_text},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            content = response.choices[0].message.content or "{}"
        except APIError as exc:
            raise RuntimeError("Hosted provider classification failed") from exc

        for attempt in range(2):
            try:
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise ValueError("provider payload is not an object")
                return Classification.model_validate(payload).model_dump()
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                if attempt == 1:
                    raise RuntimeError("Hosted provider returned invalid structured output") from exc
                try:
                    repair = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {
                                "role": "system",
                                "content": "Repair the supplied value into valid JSON with exactly category, urgency, confidence, and queue. Category must be billing, technical, or account; urgency must be low, medium, high, or critical.",
                            },
                            {"role": "user", "content": content},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0,
                    )
                    content = repair.choices[0].message.content or "{}"
                except APIError as repair_exc:
                    raise RuntimeError("Hosted provider repair attempt failed") from repair_exc

    def health_check(self) -> bool:
        return bool(OPENAI_API_KEY)
