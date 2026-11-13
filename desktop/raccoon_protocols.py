"""Responses and Anthropic Messages over Raccoon's OpenAI Chat engine.

This module is a local protocol adapter. It does not modify the pinned engine.
"""
import json
import threading
import time
import uuid
from collections import OrderedDict

from raccoon_adapter import check_model_echo

_history = OrderedDict()
_history_lock = threading.Lock()
_HISTORY_LIMIT = 128
_HISTORY_TTL = 3600


def _id(prefix):
    return prefix + uuid.uuid4().hex


def _text_parts(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("unsupported message content")
    parts = []
    for item in content:
        kind = item.get("type") if isinstance(item, dict) else None
        if kind in ("input_text", "output_text", "text"):
            parts.append({"type": "text", "text": item.get("text", "")})
        elif kind in ("input_image", "image"):
            source = item.get("source") if kind == "image" else None
            url = item.get("image_url") if kind == "input_image" else None
            if source:
                if source.get("type") != "base64":
                    raise ValueError("only base64 images are supported")
                url = "data:" + source.get("media_type", "image/png") + ";base64," + source.get("data", "")
            if isinstance(url, dict):
                url = url.get("url")
            if not isinstance(url, str) or not url:
                raise ValueError("image_url required")
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            raise ValueError("unsupported content block: " + str(kind))
    return parts


def _from_responses_input(value):
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if not isinstance(value, list):
        raise ValueError("input must be text or an item list")
    messages = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid input item")
        kind = item.get("type")
        if kind == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": item.get("call_id"), "content": str(item.get("output", ""))})
        elif kind == "function_call":
            messages.append({"role": "assistant", "content": None,
                             "tool_calls": [{"id": item.get("call_id") or item.get("id"), "type": "function",
                                             "function": {"name": item.get("name"), "arguments": item.get("arguments", "{}")}}]})
        elif item.get("role") in ("user", "assistant", "system", "developer"):
            messages.append({"role": "system" if item["role"] == "developer" else item["role"],
                             "content": _text_parts(item.get("content", ""))})
        else:
            raise ValueError("unsupported input item: " + str(kind))
    return messages


def _from_anthropic_messages(value):
    if not isinstance(value, list):
        raise ValueError("messages must be a list")
    out = []
    for item in value:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            raise ValueError("invalid Anthropic message")
        role = item["role"]
        content = item.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise ValueError("invalid Anthropic content")
        plain = [part for part in content if part.get("type") in ("text", "image")]
        if role == "assistant":
            calls = []
            for part in content:
                if part.get("type") == "tool_use":
                    calls.append({"id": part.get("id"), "type": "function",
                                  "function": {"name": part.get("name"),
                                               "arguments": json.dumps(part.get("input", {}), ensure_ascii=False)}})
                elif part.get("type") not in ("text", "image"):
                    raise ValueError("unsupported assistant block")
            msg = {"role": "assistant", "content": _text_parts(plain) if plain else None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for part in content:
                if part.get("type") == "tool_result":
                    result = part.get("content", "")
                    if isinstance(result, list):
                        result = "\n".join(str(x.get("text", "")) for x in result if x.get("type") == "text")
                    out.append({"role": "tool", "tool_call_id": part.get("tool_use_id"), "content": str(result)})
                elif part.get("type") not in ("text", "image"):
                    raise ValueError("unsupported user block")
            if plain:
                out.append({"role": "user", "content": _text_parts(plain)})
    return out


def to_chat(path, payload, native):
    """Return a Chat request and the conversation messages used for it."""
    if path == "/v1/responses":
        previous = payload.get("previous_response_id")
        messages = []
        if previous:
            with _history_lock:
                record = _history.get(previous)
                if record and time.monotonic() - record[0] < _HISTORY_TTL and record[1] == native:
                    messages = list(record[2])
                else:
                    raise LookupError("previous_response_id unavailable")
        instructions = payload.get("instructions")
        if instructions:
            messages.insert(0, {"role": "system", "content": str(instructions)})
        messages += _from_responses_input(payload.get("input", ""))
        tools = []
        for tool in payload.get("tools", []):
            if tool.get("type") != "function":
                raise ValueError("only function tools are supported")
            tools.append({"type": "function", "function": {"name": tool.get("name"),
                          "description": tool.get("description", ""), "parameters": tool.get("parameters", {})}})
        choice = payload.get("tool_choice")
        if isinstance(choice, dict):
            choice = {"type": "function", "function": {"name": choice.get("name")}}
        limit = payload.get("max_output_tokens")
    elif path == "/v1/messages":
        messages = _from_anthropic_messages(payload.get("messages"))
        system = payload.get("system")
        if isinstance(system, list):
            system = "\n".join(str(part.get("text", "")) for part in system if part.get("type") == "text")
        if system:
            messages.insert(0, {"role": "system", "content": str(system)})
        tools = []
        for tool in payload.get("tools", []):
            tools.append({"type": "function", "function": {"name": tool.get("name"),
                          "description": tool.get("description", ""), "parameters": tool.get("input_schema", {})}})
        choice = payload.get("tool_choice")
        if isinstance(choice, dict):
            if choice.get("type") == "tool":
                choice = {"type": "function", "function": {"name": choice.get("name")}}
            else:
                choice = "required" if choice.get("type") == "any" else choice.get("type", "auto")
        limit = payload.get("max_tokens")
    else:
        raise ValueError("unknown adapter protocol")
    body = {"model": native, "messages": messages, "stream": bool(payload.get("stream"))}
    if limit is not None:
        body["max_tokens"] = limit
    for field in ("temperature", "top_p"):
        if payload.get(field) is not None:
            body[field] = payload[field]
    if path == "/v1/messages" and payload.get("stop_sequences"):
        body["stop"] = payload["stop_sequences"]
    if tools:
        body["tools"] = tools
    if choice:
        body["tool_choice"] = choice
    return body, messages


def _chat_output(chat):
    choice = chat.get("choices", [{}])[0]
    message = choice.get("message") or {}
    return message, choice.get("finish_reason"), chat.get("usage") or {}


def _response_object(chat, model, response_id):
    message, _, usage = _chat_output(chat)
    output = []
    content = message.get("content") or ""
    if content:
        output.append({"id": _id("msg_"), "type": "message", "status": "completed", "role": "assistant",
                       "content": [{"type": "output_text", "text": content, "annotations": []}]})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        output.append({"id": call.get("response_item_id") or _id("fc_"), "type": "function_call", "status": "completed",
                       "call_id": call.get("id"), "name": fn.get("name"), "arguments": fn.get("arguments", "{}")})
    return {"id": response_id, "object": "response", "created_at": int(time.time()),
            "status": "completed", "model": model, "output": output, "output_text": content,
            "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                      "output_tokens": usage.get("completion_tokens", 0),
                      "total_tokens": usage.get("total_tokens", 0)}}


def _anthropic_object(chat, model):
    message, finish, usage = _chat_output(chat)
    content = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        content.append({"type": "tool_use", "id": call.get("id"), "name": fn.get("name"), "input": args})
    stop = "tool_use" if any(x["type"] == "tool_use" for x in content) else "max_tokens" if finish == "length" else "end_turn"
    return {"id": _id("msg_"), "type": "message", "role": "assistant", "model": model,
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                      "output_tokens": usage.get("completion_tokens", 0)}}


def from_chat(path, chat, model, messages, response_id=None):
    if path == "/v1/messages":
        return _anthropic_object(chat, model)
    response_id = response_id or _id("resp_")
    result = _response_object(chat, model, response_id)
    with _history_lock:
        _history[response_id] = (time.monotonic(), model.removesuffix("@raccoon"), messages + [chat.get("choices", [{}])[0].get("message", {})])
        while len(_history) > _HISTORY_LIMIT:
            _history.popitem(last=False)
    return result


def sse(name, data):
    return ("event: " + name + "\ndata: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")


async def chat_sse_events(first, stream):
    buffer = first
    async def source():
        yield b""
        async for chunk in stream:
            yield chunk
    async for chunk in source():
        buffer += chunk
        while True:
            lf = buffer.find(b"\n\n")
            crlf = buffer.find(b"\r\n\r\n")
            if lf < 0 and crlf < 0:
                break
            if crlf >= 0 and (lf < 0 or crlf < lf):
                frame, buffer = buffer[:crlf], buffer[crlf + 4:]
            else:
                frame, buffer = buffer[:lf], buffer[lf + 2:]
            for line in frame.decode("utf-8", "replace").splitlines():
                if line.startswith("data: "):
                    if line[6:] == "[DONE]":
                        return
                    yield json.loads(line[6:])
        if len(buffer) > 1024 * 1024:
            raise ValueError("Raccoon stream event too large")


async def adapted_stream(path, first, stream, native, model, messages):
    """Translate Chat deltas to typed SSE without collecting the text first."""
    response_id = _id("resp_")
    message_id = _id("msg_")
    text = ""
    calls = {}
    finish = "stop"
    usage = {}
    started_text = False
    text_stopped = False
    block_index = 0
    if path == "/v1/responses":
        yield sse("response.created", {"type": "response.created", "response": {"id": response_id,
                  "object": "response", "status": "in_progress", "model": model, "output": []}})
    else:
        yield sse("message_start", {"type": "message_start", "message": {"id": message_id,
                  "type": "message", "role": "assistant", "model": model, "content": [],
                  "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}})
    async for event in chat_sse_events(first, stream):
        check_model_echo(native, event)
        if event.get("usage"):
            usage = event["usage"]
        for choice in event.get("choices") or []:
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            delta = choice.get("delta") or {}
            part = delta.get("content")
            if isinstance(part, str) and part:
                if not started_text:
                    started_text = True
                    if path == "/v1/responses":
                        yield sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                                  "item": {"id": message_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}})
                        yield sse("response.content_part.added", {"type": "response.content_part.added", "item_id": message_id,
                                  "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})
                    else:
                        yield sse("content_block_start", {"type": "content_block_start", "index": 0,
                                  "content_block": {"type": "text", "text": ""}})
                text += part
                if path == "/v1/responses":
                    yield sse("response.output_text.delta", {"type": "response.output_text.delta", "item_id": message_id,
                              "output_index": 0, "content_index": 0, "delta": part})
                else:
                    yield sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                              "delta": {"type": "text_delta", "text": part}})
            for tool in delta.get("tool_calls") or []:
                index = tool.get("index", 0)
                state = calls.get(index)
                fn = tool.get("function") or {}
                if state is None:
                    if started_text and not text_stopped:
                        text_stopped = True
                        if path == "/v1/responses":
                            yield sse("response.output_text.done", {"type": "response.output_text.done", "item_id": message_id,
                                      "output_index": 0, "content_index": 0, "text": text})
                            yield sse("response.content_part.done", {"type": "response.content_part.done", "item_id": message_id,
                                      "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": text, "annotations": []}})
                        else:
                            yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
                    state = calls[index] = {"id": tool.get("id") or _id("call_"), "name": fn.get("name"), "arguments": ""}
                    block_index = (1 if started_text else 0) + len(calls) - 1
                    state["block_index"] = block_index
                    state["item_id"] = _id("fc_")
                    if path == "/v1/responses":
                        yield sse("response.output_item.added", {"type": "response.output_item.added", "output_index": block_index,
                                  "item": {"id": state["item_id"], "type": "function_call", "status": "in_progress",
                                           "call_id": state["id"], "name": state["name"], "arguments": ""}})
                    else:
                        yield sse("content_block_start", {"type": "content_block_start", "index": block_index,
                                  "content_block": {"type": "tool_use", "id": state["id"], "name": state["name"], "input": {}}})
                state["id"] = tool.get("id") or state["id"]
                state["name"] = fn.get("name") or state["name"]
                args = fn.get("arguments") or ""
                if args:
                    state["arguments"] += args
                    if path == "/v1/responses":
                        yield sse("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta",
                                  "item_id": state["item_id"], "output_index": state["block_index"], "delta": args})
                    else:
                        yield sse("content_block_delta", {"type": "content_block_delta", "index": state["block_index"],
                                  "delta": {"type": "input_json_delta", "partial_json": args}})
    tool_calls = [{"id": state["id"], "response_item_id": state["item_id"], "type": "function", "function": {"name": state["name"],
                   "arguments": state["arguments"] or "{}"}} for _, state in sorted(calls.items())]
    chat = {"choices": [{"message": {"role": "assistant", "content": text or None, "tool_calls": tool_calls},
                         "finish_reason": finish}], "usage": usage}
    if path == "/v1/responses":
        result = from_chat(path, chat, model, messages, response_id)
        if started_text:
            for item in result["output"]:
                if item["type"] == "message":
                    item["id"] = message_id
                    break
        if started_text and not text_stopped:
            yield sse("response.output_text.done", {"type": "response.output_text.done", "item_id": message_id,
                      "output_index": 0, "content_index": 0, "text": text})
            yield sse("response.content_part.done", {"type": "response.content_part.done", "item_id": message_id,
                      "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": text, "annotations": []}})
        for index, item in enumerate(result["output"]):
            if item["type"] == "function_call":
                yield sse("response.function_call_arguments.done", {"type": "response.function_call_arguments.done",
                          "item_id": item["id"], "output_index": index, "arguments": item["arguments"]})
            yield sse("response.output_item.done", {"type": "response.output_item.done", "output_index": index, "item": item})
        yield sse("response.completed", {"type": "response.completed", "response": result})
    else:
        if started_text and not text_stopped:
            yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        for state in calls.values():
            yield sse("content_block_stop", {"type": "content_block_stop", "index": state["block_index"]})
        reason = "tool_use" if calls else "max_tokens" if finish == "length" else "end_turn"
        yield sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None},
                  "usage": {"output_tokens": usage.get("completion_tokens", 0)}})
        yield sse("message_stop", {"type": "message_stop"})
