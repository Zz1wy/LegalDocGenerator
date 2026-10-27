"""LLM Provider 抽象、HTTP 客户端与重排。"""

import json

import httpx
import pytest

from llm import LLMError, LLMResult, get_llm_client, get_model_name, reset_llm_client
from llm.ollama import OllamaClient
from llm.openai_compat import OpenAICompatClient
from rag.rerank import _parse_ranking, llm_rerank

REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    for name in (
        "LLM_PROVIDER", "LLM_MODEL", "OLLAMA_MODEL", "LLM_BASE_URL",
        "LLM_API_KEY", "LLM_TIMEOUT", "OLLAMA_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_llm_client()
    yield
    reset_llm_client()


def patch_transport(monkeypatch, handler):
    """让 httpx.AsyncClient 使用 MockTransport，避免真实网络请求。"""

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def collect(agen):
    return [item async for item in agen]


def test_factory_defaults_to_ollama(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    client = get_llm_client()
    assert isinstance(client, OllamaClient)
    assert client.model == "qwen2.5:7b"


def test_factory_model_priority(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-model")
    assert get_model_name() == "legacy-model"
    monkeypatch.setenv("LLM_MODEL", "new-model")
    assert get_model_name() == "new-model"


def test_factory_selects_openai_compatible(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    client = get_llm_client()
    assert isinstance(client, OpenAICompatClient)
    assert client.base_url == "https://api.deepseek.com/v1"
    assert client.model == "deepseek-chat"


def test_factory_requires_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        get_llm_client()


def test_ollama_generate(monkeypatch):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={
            "response": "生成结果",
            "prompt_eval_count": 12,
            "eval_count": 5,
        })

    patch_transport(monkeypatch, handler)
    client = OllamaClient("http://ollama:11434", "qwen2.5:7b")
    result = __import__("asyncio").run(
        client.generate(
            "prompt",
            system="你是法律助手",
            temperature=0.2,
            max_tokens=128,
            json_mode=True,
        )
    )

    assert captured["url"] == "http://ollama:11434/api/generate"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["system"] == "你是法律助手"
    assert captured["payload"]["options"] == {"temperature": 0.2, "num_predict": 128}
    assert result.text == "生成结果"
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 5
    assert result.latency_ms >= 0


def test_ollama_stream(monkeypatch):
    body = "\n".join([
        json.dumps({"response": "甲"}),
        json.dumps({"response": "乙"}),
        json.dumps({"response": ""}),
    ]) + "\n"

    patch_transport(monkeypatch, lambda request: httpx.Response(200, content=body.encode()))
    client = OllamaClient("http://ollama:11434", "qwen2.5:7b")
    assert __import__("asyncio").run(collect(client.stream("prompt"))) == ["甲", "乙"]


def test_ollama_http_error_is_wrapped(monkeypatch):
    patch_transport(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    client = OllamaClient("http://ollama:11434", "qwen2.5:7b")
    with pytest.raises(LLMError, match="500"):
        __import__("asyncio").run(client.generate("prompt"))


def test_openai_generate(monkeypatch):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "deepseek-chat",
            "choices": [{"message": {"content": "云模型结果"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        })

    patch_transport(monkeypatch, handler)
    client = OpenAICompatClient(
        "https://api.deepseek.com/v1", "sk-test", "deepseek-chat"
    )
    result = __import__("asyncio").run(
        client.generate("prompt", system="系统", temperature=0.0, max_tokens=64, json_mode=True)
    )

    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert captured["payload"]["messages"] == [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "prompt"},
    ]
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_tokens"] == 64
    assert result.text == "云模型结果"
    assert result.prompt_tokens == 7
    assert result.completion_tokens == 3


def test_openai_stream(monkeypatch):
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "甲"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "乙"}}]}),
        "data: [DONE]",
    ]
    body = "\n".join(lines) + "\n"

    patch_transport(monkeypatch, lambda request: httpx.Response(200, content=body.encode()))
    client = OpenAICompatClient("https://api.deepseek.com/v1", "sk-test", "deepseek-chat")
    assert __import__("asyncio").run(collect(client.stream("prompt"))) == ["甲", "乙"]


def test_openai_bad_json_is_wrapped(monkeypatch):
    patch_transport(monkeypatch, lambda request: httpx.Response(200, json={"choices": []}))
    client = OpenAICompatClient("https://api.deepseek.com/v1", "sk-test", "deepseek-chat")
    with pytest.raises(LLMError, match="无法解析"):
        __import__("asyncio").run(client.generate("prompt"))


class FakeRerankClient:
    model = "fake"

    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.prompt = ""

    async def generate(self, prompt, **kwargs):
        self.prompt = prompt
        if self.error:
            raise self.error
        return LLMResult(text=self.text, model=self.model)


def citations(count=4):
    return [
        {"short_name": f"法{i}", "article": f"第{i}条", "text": f"条文内容 {i}"}
        for i in range(1, count + 1)
    ]


def test_parse_ranking_accepts_json_and_fallback():
    assert _parse_ranking('{"ranking": [3, 1, 2]}', 3) == [3, 1, 2]
    assert _parse_ranking('结果为 [2, 1]', 2) == [2, 1]
    assert _parse_ranking("not json", 3) == []
    # 越界/重复编号被过滤
    assert _parse_ranking('{"ranking": [1, 1, 9, 2]}', 3) == [1, 2]


def test_llm_rerank_reorders():
    client = FakeRerankClient(text='{"ranking": [3, 1, 4, 2]}')
    result = __import__("asyncio").run(llm_rerank("查询", citations(4), top_k=2, client=client))
    assert [item["short_name"] for item in result] == ["法3", "法1"]
    assert "查询" in client.prompt
    assert "条文内容 3" in client.prompt


def test_llm_rerank_falls_back_on_bad_ranking():
    client = FakeRerankClient(text="not json")
    result = __import__("asyncio").run(llm_rerank("查询", citations(4), top_k=2, client=client))
    assert [item["short_name"] for item in result] == ["法1", "法2"]


def test_llm_rerank_falls_back_on_error():
    client = FakeRerankClient(error=RuntimeError("model down"))
    result = __import__("asyncio").run(llm_rerank("查询", citations(4), top_k=2, client=client))
    assert [item["short_name"] for item in result] == ["法1", "法2"]


def test_llm_rerank_skips_model_when_few_candidates():
    client = FakeRerankClient(error=RuntimeError("should not be called"))
    result = __import__("asyncio").run(llm_rerank("查询", citations(2), top_k=2, client=client))
    assert [item["short_name"] for item in result] == ["法1", "法2"]
