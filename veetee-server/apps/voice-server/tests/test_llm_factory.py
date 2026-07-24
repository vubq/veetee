import pytest

from veetee_voice_server.providers.cliproxy import CliProxyApiLlmProvider
from veetee_voice_server.providers.groq import GroqCloudLlmProvider
from veetee_voice_server.providers.llm_factory import create_llm_provider
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider

pytestmark = pytest.mark.asyncio


async def test_factory_resolves_independent_llm_adapters() -> None:
    cliproxy = create_llm_provider(
        adapter="openai-compatible-cliproxyapi",
        base_url="http://127.0.0.1:8317/v1",
        model="gpt-5.6-terra",
        api_key="cliproxy-secret",
        reasoning_effort="none",
        config={"responseFormat": "json_schema"},
    )
    groq = create_llm_provider(
        adapter="groq-cloud",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key="groq-secret",
        reasoning_effort="none",
        config={},
    )
    generic = create_llm_provider(
        adapter="openai-compatible-9router",
        base_url="http://127.0.0.1:20128/v1",
        model="cx/gpt-5.6-terra",
        api_key="router-secret",
        reasoning_effort="none",
        config={},
    )

    assert isinstance(cliproxy, CliProxyApiLlmProvider)
    assert isinstance(groq, GroqCloudLlmProvider)
    assert type(generic) is NineRouterLlmProvider

    await cliproxy.close()
    await groq.close()
    await generic.close()
