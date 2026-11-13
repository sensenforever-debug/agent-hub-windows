"""Authenticated loopback API for all installed agent adapters."""
import hmac
import json
import secrets
from urllib.parse import urlparse

import httpx
import keyring
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from raccoon_adapter import INTERNAL_KEY, catalog, check_model_echo
from raccoon_protocols import adapted_stream, from_chat, to_chat

SERVICE = "agent-gateway-workbuddy-node"
LOCAL_KEY_NAME = "unified-local-key"
MAX_BODY = 4 * 1024 * 1024
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
UPSTREAMS = {"workbuddy": "http://127.0.0.1:8790", "raccoon": "http://127.0.0.1:8791"}


def local_key():
    value = keyring.get_password(SERVICE, LOCAL_KEY_NAME)
    if not value:
        value = "agl_" + secrets.token_urlsafe(32)
        keyring.set_password(SERVICE, LOCAL_KEY_NAME, value)
    return value


def authorize(request: Request):
    host = request.headers.get("host", "")
    if host not in ("127.0.0.1:8788", "localhost:8788"):
        raise HTTPException(403, "invalid host")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
        raise HTTPException(403, "invalid origin")
    bearer = request.headers.get("authorization", "")
    token = bearer.removeprefix("Bearer ").strip() if bearer.startswith("Bearer ") else request.headers.get("x-api-key", "").strip()
    if not hmac.compare_digest(token, local_key()):
        raise HTTPException(401, "invalid local API key")


async def discover(agent):
    if agent == "raccoon":
        return await catalog()
    url = UPSTREAMS[agent]
    headers = {}
    if agent == "workbuddy":
        secret = keyring.get_password(SERVICE, "bridge-token")
        if not secret:
            return []
        headers["Authorization"] = "Bearer " + secret
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.get(url + "/v1/models", headers=headers)
        response.raise_for_status()
        return [m["id"] for m in response.json().get("data", []) if isinstance(m.get("id"), str)]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def models(request: Request):
    authorize(request)
    entries = []
    for agent in UPSTREAMS:
        try:
            for model in await discover(agent):
                entries.append({"id": model + "@" + agent, "object": "model", "owned_by": agent})
        except (httpx.HTTPError, ValueError):
            pass
    return {"object": "list", "data": entries}


async def forward(request: Request, path: str):
    authorize(request)
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "request too large")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "invalid JSON")
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
        raise HTTPException(400, "model required")
    model_id = payload["model"]
    native, sep, agent = model_id.rpartition("@")
    if not sep or agent not in UPSTREAMS or not native:
        raise HTTPException(400, "model must include an agent suffix")
    try:
        names = await discover(agent)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "agent unavailable")
    if native not in names:
        raise HTTPException(404, "model unavailable")
    payload["model"] = native
    adapter_messages = None
    upstream_path = path
    if agent == "raccoon" and path != "/v1/chat/completions":
        try:
            payload, adapter_messages = to_chat(path, payload, native)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc))
        upstream_path = "/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if agent == "workbuddy":
        secret = keyring.get_password(SERVICE, "bridge-token")
        headers.update({"Authorization": "Bearer " + secret, "x-api-key": secret})
        if path == "/v1/messages":
            headers["anthropic-version"] = request.headers.get("anthropic-version", "2023-06-01")
    else:
        headers["X-Agent-Hub-Internal"] = INTERNAL_KEY
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None), trust_env=False)
    try:
        upstream = await client.send(client.build_request("POST", UPSTREAMS[agent] + upstream_path,
                                                           headers=headers, json=payload), stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(502, "agent request failed")
    content_type = upstream.headers.get("content-type", "application/json")
    if payload.get("stream"):
        stream = upstream.aiter_raw()
        first = b""
        if agent == "raccoon" and upstream.status_code < 400:
            try:
                while b"\n\n" not in first and len(first) <= 65536:
                    first += await anext(stream)
                if len(first) > 65536:
                    raise ValueError("Raccoon stream header is too large")
                event = first.split(b"\n\n", 1)[0].decode("utf-8", "replace")
                for line in event.splitlines():
                    if line.startswith("data: ") and line[6:] != "[DONE]":
                        check_model_echo(native, json.loads(line[6:]))
            except (ValueError, StopAsyncIteration) as exc:
                await upstream.aclose()
                await client.aclose()
                raise HTTPException(502, f"Raccoon stream validation failed: {exc}")
        async def chunks():
            try:
                if adapter_messages is not None and upstream.status_code < 400:
                    async for chunk in adapted_stream(path, first, stream, native, model_id, adapter_messages):
                        yield chunk
                else:
                    if first:
                        yield first
                    async for chunk in stream:
                        yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(chunks(), status_code=upstream.status_code,
                                 media_type="text/event-stream" if adapter_messages is not None and upstream.status_code < 400 else content_type)
    data = await upstream.aread()
    await upstream.aclose()
    await client.aclose()
    parsed = json.loads(data) if "json" in content_type else {"raw": data.decode(errors="replace")}
    if agent == "raccoon" and upstream.status_code < 400:
        try:
            check_model_echo(native, parsed)
        except ValueError as exc:
            raise HTTPException(502, str(exc))
        if adapter_messages is not None:
            parsed = from_chat(path, parsed, model_id, adapter_messages)
    return JSONResponse(content=parsed,
                        status_code=upstream.status_code)


@app.post("/v1/chat/completions")
async def chat(request: Request):
    return await forward(request, "/v1/chat/completions")


@app.post("/v1/responses")
async def responses(request: Request):
    return await forward(request, "/v1/responses")


@app.post("/v1/messages")
async def messages(request: Request):
    return await forward(request, "/v1/messages")
