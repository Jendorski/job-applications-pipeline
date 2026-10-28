from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from jap.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from jap.llm.base import LLMError, LLMRefusal, Usage


class Out(BaseModel):
    answer: str


class StubMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def stub_client(stop_reason="end_turn", parsed=Out(answer="ok"), stop_details=None):
    response = SimpleNamespace(
        model="claude-opus-5-5", stop_reason=stop_reason, stop_details=stop_details, parsed_output=parsed,
        usage=SimpleNamespace(input_tokens=120, output_tokens=40, cache_read_input_tokens=7000, cache_creation_input_tokens=None),
    )
    messages = StubMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_request_shape_caches_system_sets_effort_and_fallbacks():
    client, messages = stub_client()
    out, usage = AnthropicProvider(client).complete(system="RULES", user="JOB", schema=Out, model="claude-opus-5-5", effort="medium")
    kw = messages.kwargs
    assert out.answer == "ok"
    assert kw["system"] == [{"type": "text", "text": "RULES", "cache_control": {"type": "ephemeral"}}]
    assert kw["messages"] == [{"role": "user", "content": "JOB"}]
    assert kw["output_config"] == {"effort": "medium"} and kw["output_format"] is Out
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert "temperature" not in kw and "tool_choice" not in kw
    assert usage == Usage(model="claude-opus-5-5", input_tokens=120, output_tokens=40, cache_read_tokens=7000)


def test_no_fallbacks_where_unsupported_or_disabled():
    client, messages = stub_client()
    AnthropicProvider(client).complete(system="s", user="u", schema=Out, model="claude-haiku-5-5", effort="low")
    assert "fallbacks" not in messages.kwargs
    AnthropicProvider(client, fallbacks=False).complete(system="s", user="u", schema=Out, model="claude-opus-5-5", effort="low")
    assert "fallbacks" not in messages.kwargs


def test_refusal_and_truncation_raise():
    client, _ = stub_client(stop_reason="refusal", parsed=None, stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(LLMRefusal, match="cyber"):
        AnthropicProvider(client).complete(system="s", user="u", schema=Out, model="claude-opus-5-5", effort="low")
    client, _ = stub_client(stop_reason="max_tokens", parsed=None)
    with pytest.raises(LLMError, match="max_tokens"):
        AnthropicProvider(client).complete(system="s", user="u", schema=Out, model="claude-opus-5-5", effort="low")


def test_openai_compat_retries_once_on_invalid_json():
    from jap.llm.openai_compat import OpenAICompatProvider

    replies = iter(['{"wrong": 1}', '{"answer": "fixed"}'])
    seen = []

    def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=next(replies)))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10, prompt_tokens_details=SimpleNamespace(cached_tokens=60)),
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), base_url="http://x")
    out, usage = OpenAICompatProvider(client=client).complete(system="s", user="u", schema=Out, model="llama", effort="low")
    assert out.answer == "fixed" and len(seen) == 2
    assert seen[0]["response_format"] == {"type": "json_object"} and "JSON Schema" in seen[0]["messages"][0]["content"]
    assert usage.input_tokens == 80 and usage.cache_read_tokens == 120 and usage.output_tokens == 20


def test_cost_from_usage():
    u = Usage(model="m", input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    assert u.cost_usd((4.0, 20.0, 0.2, 5.0)) == pytest.approx(4.0 + 2.0 + 0.2)
    assert u.cost_usd(None) is None
