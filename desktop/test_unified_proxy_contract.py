"""Contract checks for source IDs emitted by the local unified API."""

import json

import httpx
from fastapi.testclient import TestClient

import unified_proxy


def _proxy(monkeypatch, body, content_type="application/json"):
    original_client = httpx.AsyncClient

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body

    async def discover(agent):
        return ["sn-test"] if agent == "raccoon" else []

    def upstream(_request):
        return httpx.Response(200, stream=Stream(), headers={"content-type": content_type})

    def client(*args, **kwargs):
        return original_client(transport=httpx.MockTransport(upstream), **kwargs)

    monkeypatch.setattr(unified_proxy, "authorize", lambda _request: None)
    monkeypatch.setattr(unified_proxy, "discover", discover)
    monkeypatch.setattr(unified_proxy.httpx, "AsyncClient", client)
    return TestClient(unified_proxy.app)


def test_chat_json_emits_source_model(monkeypatch):
    with _proxy(monkeypatch, b'{"model":"sn-test","choices":[]}') as client:
        response = client.post("/v1/chat/completions", json={"model": "sn-test@raccoon", "messages": []})
    assert response.status_code == 200
    assert response.json()["model"] == "sn-test@raccoon"


def test_chat_sse_emits_source_model(monkeypatch):
    events = (b'data: {"model":"sn-test","choices":[{"delta":{"content":"Hi"}}]}\n\n'
              b'data: [DONE]\n\n')
    with _proxy(monkeypatch, events, "text/event-stream") as client:
        response = client.post("/v1/chat/completions", json={"model": "sn-test@raccoon", "messages": [], "stream": True})
    assert response.status_code == 200
    assert json.loads(response.text.splitlines()[0][6:])["model"] == "sn-test@raccoon"
    assert "data: [DONE]" in response.text


def test_raccoon_directory_survives_workbuddy_failure(monkeypatch):
    async def discover(agent):
        if agent == "workbuddy":
            raise httpx.ConnectError("bridge offline")
        return ["sn-test"]

    monkeypatch.setattr(unified_proxy, "authorize", lambda _request: None)
    monkeypatch.setattr(unified_proxy, "discover", discover)
    with TestClient(unified_proxy.app) as client:
        response = client.get("/v1/models")
    assert response.status_code == 200
    assert [entry["id"] for entry in response.json()["data"]] == ["sn-test@raccoon"]
