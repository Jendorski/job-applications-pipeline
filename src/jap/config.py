import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(os.environ.get("JAP_HOME", Path.cwd()))
PROFILE_PATH = ROOT / "profile" / "master_profile.yaml"
TRACKS_PATH = ROOT / "profile" / "tracks.yaml"
EXAMPLE_PROFILE_DIR = ROOT / "examples" / "profile"  # copied to profile/ by `jap init`
TEMPLATES_DIR = ROOT / "templates"
APPLICATIONS_DIR = ROOT / "applications"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "pipeline.db"
LOG_DIR = DATA_DIR / "logs"
HEARTBEAT_PATH = DATA_DIR / "heartbeat"
INBOX_DIR = ROOT / "inbox"
POLL_SECONDS = int(os.environ.get("POLL_SECONDS") or 20)

CV_MAX_PAGES = 2
LETTER_MAX_PAGES = 1

# USD per million tokens: (input, output, cache read, cache write).
# Source: claude-api skill model table, cached 2026-10-06. Cache write = 1.25x input (5-minute TTL).
# Haiku 5.5 rates are for prompts up to 100K tokens; its cache-read rate is not published there,
# so 0.1x input is an estimate. Override or extend with LLM_PRICES='{"model": [in, out, read, write]}'.
DEFAULT_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    provider: str = "anthropic"  # anthropic | openai_compat
    model_extract: str = "claude-opus-5-5"
    model_tailor: str = "claude-opus-5-5"
    effort_extract: str = "low"
    effort_tailor: str = "medium"
    fallbacks: bool = True  # server-side refusal fallbacks on models that support them
    # Reasoning models count their thinking toward this, and the tailor call is the long one.
    # Claude stays at 16000: the SDK refuses larger non-streaming requests (expected >10 min).
    max_tokens_tailor: int = 16000
    openai_base_url: str | None = None
    monthly_budget_usd: float = 10.0
    prices: dict | None = None

    def price(self, model: str) -> tuple[float, float, float, float] | None:
        return (self.prices or DEFAULT_PRICES).get(model)


class ConfigError(ValueError):
    pass


def load_settings() -> Settings:
    prices = dict(DEFAULT_PRICES)
    if raw := _env("LLM_PRICES"):
        try:
            prices |= {k: tuple(float(x) for x in v) for k, v in json.loads(raw).items()}
        except (ValueError, TypeError, AttributeError) as e:
            raise ConfigError(f'LLM_PRICES must be JSON like {{"model": [in, out, cache_read, cache_write]}} ({e})') from e
    provider = _env("LLM_PROVIDER", "anthropic")
    # Claude models are the default only for the Anthropic provider; an OpenAI-compatible endpoint
    # (OpenAI, Ollama, OpenRouter, ...) has no model name that works everywhere, so both must be set.
    default_model = "claude-opus-5-5" if provider == "anthropic" else None
    model_extract = _env("MODEL_EXTRACT", default_model)
    model_tailor = _env("MODEL_TAILOR", default_model)
    if not (model_extract and model_tailor):
        missing = [n for n, v in (("MODEL_EXTRACT", model_extract), ("MODEL_TAILOR", model_tailor)) if not v]
        raise ConfigError(f"LLM_PROVIDER={provider} needs {' and '.join(missing)} set to model names your endpoint serves")
    return Settings(
        provider=provider,
        model_extract=model_extract,
        model_tailor=model_tailor,
        effort_extract=_env("EFFORT_EXTRACT", "low"),
        effort_tailor=_env("EFFORT_TAILOR", "medium"),
        fallbacks=_env("LLM_FALLBACKS", "1") == "1",
        max_tokens_tailor=int(_env("MAX_TOKENS_TAILOR", "16000" if provider == "anthropic" else "32000")),
        openai_base_url=_env("OPENAI_BASE_URL"),
        monthly_budget_usd=float(_env("MONTHLY_BUDGET_USD", "10")),
        prices=prices,
    )
