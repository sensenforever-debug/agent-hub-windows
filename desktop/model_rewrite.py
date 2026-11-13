"""Rewrite only protocol model fields while preserving SSE event boundaries."""

import json


class ModelMismatch(Exception):
    """A successful upstream response identified a different model."""


def validate_model_fields(value, native_model: str, require_model=False):
    if not isinstance(value, dict):
        if require_model:
            raise ModelMismatch('upstream response did not identify a model')
        return
    actual = value.get('model')
    if require_model and (not isinstance(actual, str) or not actual):
        raise ModelMismatch('upstream response did not identify a model')
    if isinstance(actual, str) and actual and actual.lower() != native_model.lower():
        raise ModelMismatch('upstream model does not match the requested source')
    for key in ('response', 'message', 'data'):
        if isinstance(value.get(key), dict):
            validate_model_fields(value[key], native_model)


def validate_sse_frame(frame: bytes, native_model: str):
    try:
        data_lines = [line[5:].lstrip() for line in frame.decode('utf-8').splitlines()
                      if line.startswith('data:')]
        if data_lines and data_lines != ['[DONE]']:
            validate_model_fields(json.loads('\n'.join(data_lines)), native_model)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelMismatch('invalid upstream SSE event') from exc


def public_model_fields(value, source_model: str, public_model: str):
    if not isinstance(value, dict):
        return value
    result = value.copy()
    native_model = source_model.rsplit('@', 1)[0] if '@' in source_model else source_model
    if result.get('model') in (source_model, native_model):
        result['model'] = public_model
    for key in ('response', 'message', 'data'):
        if isinstance(result.get(key), dict):
            result[key] = public_model_fields(result[key], source_model, public_model)
    return result


class ModelResponseRewriter:
    def __init__(self, source_model: str, public_model: str, content_type: str, max_buffer: int):
        self.source_model = source_model
        self.public_model = public_model
        self.max_buffer = max_buffer
        if source_model == public_model and '@' not in source_model:
            self.kind = 'passthrough'
        elif 'text/event-stream' in content_type:
            self.kind = 'sse'
        elif 'json' in content_type:
            self.kind = 'json'
        else:
            self.kind = 'passthrough'
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        if self.kind == 'passthrough':
            return [chunk]
        self.buffer.extend(chunk)
        if len(self.buffer) > self.max_buffer:
            raise ValueError('response event exceeds rewrite buffer')
        if self.kind == 'json':
            return []
        output = []
        while True:
            lf = self.buffer.find(b'\n\n')
            crlf = self.buffer.find(b'\r\n\r\n')
            choices = [(lf, 2), (crlf, 4)]
            choices = [(position, size) for position, size in choices if position >= 0]
            if not choices:
                break
            position, size = min(choices)
            frame = bytes(self.buffer[:position])
            del self.buffer[:position + size]
            output.append(self._event(frame, b'\r\n\r\n' if size == 4 else b'\n\n'))
        return output

    def finish(self) -> list[bytes]:
        if self.kind == 'passthrough':
            return []
        data = bytes(self.buffer)
        self.buffer.clear()
        if self.kind == 'json':
            try:
                value = json.loads(data)
                adjusted = public_model_fields(value, self.source_model, self.public_model)
                return [json.dumps(adjusted, ensure_ascii=False, separators=(',', ':')).encode()]
            except (ValueError, UnicodeDecodeError):
                return [data]
        if data:
            validate_sse_frame(data, self.source_model)
        return [data] if data else []

    def _event(self, frame: bytes, separator: bytes) -> bytes:
        validate_sse_frame(frame, self.source_model)
        try:
            lines = frame.decode().splitlines()
            data_lines = [line[5:].lstrip() for line in lines if line.startswith('data:')]
            if not data_lines or data_lines == ['[DONE]']:
                return frame + separator
            value = json.loads('\n'.join(data_lines))
            adjusted = public_model_fields(value, self.source_model, self.public_model)
            payload = json.dumps(adjusted, ensure_ascii=False, separators=(',', ':'))
            kept = [line for line in lines if not line.startswith('data:')]
            return ('\n'.join([*kept, 'data: '+payload])).encode() + separator
        except (ValueError, UnicodeDecodeError):
            return frame + separator
