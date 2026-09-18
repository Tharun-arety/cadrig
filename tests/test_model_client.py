import json

from cadrig.models import ModelMessage, OpenAICompatibleClient


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"choices":[{"message":{"content":"{\\"actions\\":[]}"}}]}'


def test_openai_compatible_client_sends_key_model_and_schema(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(
        base_url="http://localhost:1234/v1/",
        model="local-cad-model",
        api_key="secret",
        timeout_seconds=3,
    )
    result = client.complete(
        [ModelMessage("user", "make a box")], response_schema={"type": "object"}
    )

    request = captured["request"]
    body = json.loads(request.data)
    assert request.full_url == "http://localhost:1234/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret"
    assert body["model"] == "local-cad-model"
    assert body["response_format"]["type"] == "json_schema"
    assert captured["timeout"] == 3
    assert result == '{"actions":[]}'
