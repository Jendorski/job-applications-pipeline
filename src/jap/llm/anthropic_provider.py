import anthropic

from jap.llm.base import LLMError, LLMRefusal, T, Usage

# Models that accept the server-side refusal fallback (`fallbacks: "default"`) on the Claude API.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.Anthropic | None = None, fallbacks: bool = True):
        self.client = client or anthropic.Anthropic(max_retries=3)
        self.fallbacks = fallbacks

    def complete(self, *, system: str, user: str, schema: type[T], model: str, effort: str,
                 max_tokens: int = 16000) -> tuple[T, Usage]:
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            # The system block (rules + profile) is byte-identical across jobs, so it caches.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort},
            output_format=schema,
        )
        if self.fallbacks and model in FALLBACK_MODELS:
            kwargs |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}

        try:
            response = self.client.beta.messages.parse(**kwargs)
        except anthropic.BadRequestError as e:
            raise LLMError(f"bad request: {e.message}") from e
        except anthropic.AuthenticationError as e:
            raise LLMError("authentication failed: check ANTHROPIC_API_KEY") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"permission denied: {e.message}") from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"model or endpoint not found: {model}") from e
        except anthropic.RateLimitError as e:
            raise LLMError("rate limited after retries", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise LLMError("network error reaching the Claude API", retryable=True) from e
        except TypeError as e:
            if "authentication" not in str(e):
                raise
            raise LLMError("no Claude credentials: set ANTHROPIC_API_KEY in .env, or run `ant auth login`") from e

        u = response.usage
        usage = Usage(
            model=response.model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise LLMRefusal(f"model declined the request (category: {category})")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"output hit max_tokens={max_tokens} before finishing")
        if response.parsed_output is None:
            raise LLMError("response did not contain the structured output")
        return response.parsed_output, usage
