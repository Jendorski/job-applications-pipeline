"""Any OpenAI-compatible chat endpoint: OpenAI, OpenRouter, Groq, Together, Ollama, LM Studio, vLLM.

Uses `response_format={"type": "json_object"}` (the widest-supported JSON mode) with the
schema in the system prompt, then validates with pydantic and retries once with the error.
"""

import json
import os

import openai
from pydantic import ValidationError

from jap.config import ConfigError
from jap.llm.base import LLMError, T, Usage


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str | None = None, client: openai.OpenAI | None = None):
        # Local servers (Ollama, LM Studio, vLLM) ignore the key, but the SDK refuses to start without one.
        api_key = os.environ.get("OPENAI_API_KEY") or ("not-needed" if base_url else None)
        if client is None and api_key is None:
            raise ConfigError("LLM_PROVIDER=openai_compat needs OPENAI_API_KEY, or OPENAI_BASE_URL for a local server")
        self.client = client or openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=3)

    def complete(self, *, system: str, user: str, schema: type[T], model: str, effort: str,
                 max_tokens: int = 16000) -> tuple[T, Usage]:
        instructions = (
            f"{system}\n\nRespond with one JSON object, and nothing else, that validates against this JSON Schema:\n"
            f"{json.dumps(schema.model_json_schema(), sort_keys=True)}"
        )
        messages = [{"role": "system", "content": instructions}, {"role": "user", "content": user}]
        total = Usage(model=model, input_tokens=0, output_tokens=0)
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, response_format={"type": "json_object"},
                )
            except openai.RateLimitError as e:
                raise LLMError("rate limited after retries", retryable=True) from e
            except openai.APIStatusError as e:
                raise LLMError(f"API error {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
            except openai.APIConnectionError as e:
                raise LLMError(f"network error reaching {self.client.base_url}", retryable=True) from e

            if response.usage:
                cached = getattr(response.usage.prompt_tokens_details, "cached_tokens", 0) or 0
                total.input_tokens += response.usage.prompt_tokens - cached
                total.cache_read_tokens += cached
                total.output_tokens += response.usage.completion_tokens
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise LLMError(f"output hit max_tokens={max_tokens} before finishing")
            content = choice.message.content or ""
            try:
                return schema.model_validate_json(content), total
            except ValidationError as e:
                if attempt:
                    raise LLMError(f"output did not match the schema: {e}") from e
                messages += [{"role": "assistant", "content": content},
                             {"role": "user", "content": f"That JSON failed validation:\n{e}\nReturn the corrected JSON object only."}]
        raise AssertionError("unreachable")
