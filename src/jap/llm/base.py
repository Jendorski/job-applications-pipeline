from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Usage(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def cost_usd(self, price: tuple[float, float, float, float] | None) -> float | None:
        if price is None:
            return None
        p_in, p_out, p_read, p_write = price
        return (self.input_tokens * p_in + self.output_tokens * p_out
                + self.cache_read_tokens * p_read + self.cache_write_tokens * p_write) / 1_000_000


class LLMError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMRefusal(LLMError):
    pass


class LLMProvider(Protocol):
    name: str

    def complete(self, *, system: str, user: str, schema: type[T], model: str, effort: str,
                 max_tokens: int = 16000) -> tuple[T, Usage]:
        """One structured call. `system` is the stable, cacheable prefix; `user` is per-job."""
        ...
