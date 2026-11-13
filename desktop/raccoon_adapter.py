"""Raccoon account and catalog adapter around the pinned raccoon2api engine.

The upstream engine owns inference. This layer only discovers models from the
live catalog and guards against the upstream's unknown-model fallback.
"""
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import keyring

CATALOG_URL = "https://xiaohuanxiong.com/api/web/llm/v2/model_catalog"
INTERNAL_KEY = secrets.token_urlsafe(32)
AUTH_FILE = Path(os.environ.get("APPDATA", Path.home())) / "office-raccoon/agent/config/auth.json"
DESKTOP_AUTH_FILE = Path.home() / ".office-raccoon/agent/config/auth.json"
BOX_AUTH_FILE = Path.home() / ".box-agent/config/auth.json"
_cache = {"until": 0.0, "models": []}


def access_token():
    record = auth_record()
    if record:
        return record["access_token"]
    return keyring.get_password("agent-gateway-workbuddy-node", "raccoon-access-token")


def auth_record():
    paths = [Path(os.environ["RACCOON_AUTH_FILE"])] if os.environ.get("RACCOON_AUTH_FILE") else [DESKTOP_AUTH_FILE, AUTH_FILE, BOX_AUTH_FILE]
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("access_token"), str) and data["access_token"]:
                return data
        except (OSError, ValueError, AttributeError):
            pass
    return None


def account_identity():
    record = auth_record()
    identity = record.get("office_identity") if record else None
    return identity if isinstance(identity, str) and identity else None


def save_token(value):
    token = value.strip()
    if not token or len(token) < 20 or any(c.isspace() for c in token):
        raise ValueError("请输入有效的小浣熊会话令牌")
    keyring.set_password("agent-gateway-workbuddy-node", "raccoon-access-token", token)
    _cache.update(until=0.0, models=[])


async def catalog(force=False):
    token = access_token()
    if not token:
        _cache.update(until=0.0, models=[])
        return []
    if not force and time.monotonic() < _cache["until"]:
        return list(_cache["models"])
    async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
        response = await client.get(CATALOG_URL, headers={"Authorization": "Bearer " + token,
                                                          "Accept": "application/json"})
        response.raise_for_status()
        body = response.json()
    categories = body.get("data", {}).get("categories") if isinstance(body, dict) else None
    if not isinstance(categories, list):
        raise ValueError("Raccoon catalog response has no categories")
    names = []
    seen = set()
    for category in categories:
        for item in category.get("models", []) if isinstance(category, dict) else []:
            name = item.get("name") or item.get("model_name") if isinstance(item, dict) else None
            if isinstance(name, str) and name and name.lower() not in seen:
                names.append(name)
                seen.add(name.lower())
    if not names:
        raise ValueError("Raccoon catalog is empty")
    _cache.update(until=time.monotonic() + 60, models=names)
    return list(names)


def check_model_echo(expected, payload):
    """Reject a successful response that silently used another model."""
    actual = payload.get("model") if isinstance(payload, dict) else None
    if isinstance(actual, str) and actual and actual.lower() != expected.lower():
        raise ValueError(f"Raccoon returned a different model: {actual}")
