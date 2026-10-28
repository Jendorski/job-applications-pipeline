from collections.abc import Callable

from jap.llm.base import T, Usage


class FakeProvider:
    """Test double: returns queued responses per schema and records every call."""

    name = "fake"

    def __init__(self, responses: dict[type, list | Callable]):
        self.responses = responses
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, schema: type[T], model: str, effort: str,
                 max_tokens: int = 16000) -> tuple[T, Usage]:
        self.calls.append({"schema": schema, "system": system, "user": user, "model": model, "effort": effort})
        source = self.responses[schema]
        value = source(user) if callable(source) else source.pop(0)
        return value, Usage(model=model, input_tokens=1000, output_tokens=500, cache_read_tokens=800)
