import pytest

from jap import config
from jap.llm import get_provider

ENV = ["LLM_PROVIDER", "MODEL_EXTRACT", "MODEL_TAILOR", "OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_PRICES"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_anthropic_defaults_to_opus():
    s = config.load_settings()
    assert (s.provider, s.model_extract, s.model_tailor) == ("anthropic", "claude-opus-5-5", "claude-opus-5-5")


def test_openai_compat_has_no_default_models(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    with pytest.raises(config.ConfigError, match="MODEL_EXTRACT and MODEL_TAILOR"):
        config.load_settings()
    monkeypatch.setenv("MODEL_EXTRACT", "llama3.1")
    with pytest.raises(config.ConfigError, match="MODEL_TAILOR"):
        config.load_settings()


def test_openai_compat_local_server_needs_no_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("MODEL_EXTRACT", "llama3.1")
    monkeypatch.setenv("MODEL_TAILOR", "llama3.1:70b")
    with pytest.raises(config.ConfigError, match="OPENAI_API_KEY"):
        get_provider(config.load_settings())
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    s = config.load_settings()
    assert get_provider(s).name == "openai_compat" and s.price(s.model_tailor) is None


def test_bad_llm_prices_is_a_config_error(monkeypatch):
    monkeypatch.setenv("LLM_PRICES", '{"deepseek-flash": [IN, OUT]}')
    with pytest.raises(config.ConfigError, match="LLM_PRICES"):
        config.load_settings()
