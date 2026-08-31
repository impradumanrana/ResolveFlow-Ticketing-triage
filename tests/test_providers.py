from types import SimpleNamespace

from app.providers import OpenAIProvider


class FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        content = "not-json" if self.calls == 1 else (
            '{"category":"technical","urgency":"low","confidence":0.91,"queue":"Tier-1 Technical"}'
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
