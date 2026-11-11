import pytest
from langchain_openai import ChatOpenAI

from ai_agent_template.agent.models import build_chat_model
from ai_agent_template.core.settings import Settings


def test_bailian_requires_key_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("AI_AGENT_BAILIAN_BASE_URL", raising=False)
    settings = Settings(_env_file=None, model_provider="bailian", model_name="qwen-flash")
    with pytest.raises(ValueError, match="BAILIAN_API_KEY"):
        build_chat_model(settings)


def test_bailian_uses_compatible_endpoint() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="bailian",
        model_name="qwen-flash",
        BAILIAN_API_KEY="example-only",
        bailian_base_url="https://example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    model = build_chat_model(settings)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "qwen-flash"
    assert model.openai_api_base is not None
    assert model.openai_api_base.startswith(
        "https://example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
