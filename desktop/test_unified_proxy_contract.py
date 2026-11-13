"""Contract checks for source IDs emitted by the local unified API."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import unified_proxy

ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def _proxy(monkeypatch, body, content_type="application/json", agent="raccoon", raise_server_exceptions=True):

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body

    async def discover(source):
        return ["sn-test"] if source == agent else []

    def upstream(_request):
        return httpx.Response(200, stream=Stream(), headers={"content-type": content_type})

    def client(*args, **kwargs):
        return ORIGINAL_ASYNC_CLIENT(transport=httpx.MockTransport(upstream), **kwargs)

    monkeypatch.setattr(unified_proxy, "authorize", lambda _request: None)
    monkeypatch.setattr(unified_proxy, "discover", discover)
    monkeypatch.setattr(unified_proxy.keyring, "get_password", lambda *args: "local-test-key")
    monkeypatch.setattr(unified_proxy.httpx, "AsyncClient", client)
    return TestClient(unified_proxy.app, raise_server_exceptions=raise_server_exceptions)


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


def test_workbuddy_json_rejects_wrong_upstream_model(monkeypatch):
    for path in ("/v1/chat/completions", "/v1/responses", "/v1/messages"):
        with _proxy(monkeypatch, b'{"model":"different-model","choices":[]}', agent="workbuddy") as client:
            response = client.post(path, json={"model": "sn-test@workbuddy", "messages": [], "input": "test"})
        assert response.status_code == 502
        assert "model" in response.json()["detail"]


def test_raccoon_sse_rejects_later_wrong_model(monkeypatch):
    events = (b'data: {"model":"sn-test","choices":[{"delta":{"content":"Hi"}}]}\n\n'
              b'data: {"model":"different-model","choices":[{"delta":{"content":"bad"}}]}\n\n'
              b'data: [DONE]\n\n')
    with _proxy(monkeypatch, events, "text/event-stream", raise_server_exceptions=False) as client:
        response = client.post("/v1/chat/completions", json={"model": "sn-test@raccoon", "messages": [], "stream": True})
    assert response.status_code == 200
    assert "different-model" not in response.text
    assert "[DONE]" not in response.text
    assert "upstream_model_mismatch" in response.text


@pytest.mark.parametrize("path,first,later,completion,error_event", [
    ("/v1/responses", b'event: response.created\ndata: {"response":{"model":"sn-test"}}\n\n',
     b'event: response.completed\ndata: {"response":{"model":"different-model"}}\n\n',
     "response.completed", "response.failed"),
    ("/v1/messages", b'event: message_start\ndata: {"message":{"model":"sn-test"}}\n\n',
     b'event: message_delta\ndata: {"message":{"model":"different-model"}}\n\n',
     "message_stop", "event: error"),
])
def test_workbuddy_sse_rejects_later_wrong_model(monkeypatch,path,first,later,completion,error_event):
    with _proxy(monkeypatch, first+later, "text/event-stream", agent="workbuddy",
                raise_server_exceptions=False) as client:
        response=client.post(path,json={"model":"sn-test@workbuddy","messages":[],"input":"test","stream":True})
    assert response.status_code==200
    assert "different-model" not in response.text
    assert completion not in response.text
    assert error_event in response.text


@pytest.mark.parametrize("path,error_event", [
    ("/v1/responses", "response.failed"),
    ("/v1/messages", "event: error"),
])
def test_raccoon_adapted_sse_rejects_later_wrong_model(monkeypatch,path,error_event):
    events=(b'data: {"model":"sn-test","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            b'data: {"model":"different-model","choices":[{"delta":{"content":"bad"}}]}\n\n'
            b'data: [DONE]\n\n')
    with _proxy(monkeypatch, events, "text/event-stream", raise_server_exceptions=False) as client:
        response=client.post(path,json={"model":"sn-test@raccoon","messages":[{"role":"user","content":"test"}],
                                        "input":"test","stream":True})
    assert response.status_code==200
    assert "different-model" not in response.text
    assert error_event in response.text


def test_all_three_protocols_echo_source_model(monkeypatch):
    fixtures = [
        ("/v1/chat/completions", b'{"model":"sn-test","choices":[]}'),
        ("/v1/responses", b'{"model":"sn-test","output":[]}'),
        ("/v1/messages", b'{"model":"sn-test","content":[]}'),
    ]
    for path, body in fixtures:
        with _proxy(monkeypatch, body, agent="workbuddy") as client:
            response = client.post(path, json={"model": "sn-test@workbuddy", "messages": [], "input": "test"})
        assert response.status_code == 200
        assert response.json()["model"] == "sn-test@workbuddy"


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
