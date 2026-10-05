from types import SimpleNamespace

from app.providers import OpenAIProvider


class FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        content = (
            "not-json"
            if self.calls == 1
            else (
                '{"category":"technical","urgency":"low","confidence":0.91,'
                '"queue":"Tier-1 Technical"}'
            )
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_openai_provider_uses_one_bounded_repair_attempt():
    provider = OpenAIProvider.__new__(OpenAIProvider)
    completions = FakeCompletions()
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider.model = "test-model"
    result = provider.classify("Password reset")
    assert result["category"] == "technical"
    assert completions.calls == 2


def test_openai_grounded_answer_passes_only_retrieved_evidence_to_model():
    provider = OpenAIProvider.__new__(OpenAIProvider)
    captured = {}

    class GroundedCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            content = (
                '{"answer":"Use the reset flow. [E1]","citations":["E1"],'
                '"claims":[{"claim":"Use the reset flow.","evidence_key":"E1",'
                '"support_quote":"Use the reset flow."}],"sufficient_evidence":true}'
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=GroundedCompletions()))
    provider.model = "test-model"
    result = provider.generate_grounded_answer(
        "I forgot my password",
        [
            {
                "article_id": "KB-001",
                "title": "Reset",
                "excerpt": "Use the reset flow.",
                "source": "Test",
            }
        ],
    )
    assert result["citations"] == ["KB-001"]
    assert captured["response_format"] == {"type": "json_object"}
    assert '"evidence_key": "E1"' in captured["messages"][1]["content"]
    assert result["answer"].endswith("[KB-001]")
