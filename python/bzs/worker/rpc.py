"""Strict, bounded Worker control envelopes. No business or network side effects."""
from __future__ import annotations
import json
import math
from typing import Any

MAX_BODY = 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 20000


class InvalidEnvelope(ValueError):
    """An authenticated request does not satisfy the Worker envelope contract."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEnvelope("duplicate JSON key")
        result[key] = value
    return result


def _integer(text: str) -> int:
    if len(text) > 20:
        raise InvalidEnvelope("integer outside signed 64-bit range")
    value = int(text)
    if not -(2**63) <= value < 2**63:
        raise InvalidEnvelope("integer outside signed 64-bit range")
    return value


def _constant(_text: str) -> None:
    raise InvalidEnvelope("non-finite JSON number")


def decode_envelope(body: bytes) -> tuple[str, dict[str, Any]]:
    if len(body) > MAX_BODY:
        raise InvalidEnvelope("request body too large")
    try:
        text = body.decode("utf-8", "strict")
        # Bound parser recursion before json.loads; brackets in strings are data.
        depth = 0
        quoted = escaped = False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > MAX_DEPTH:
                    raise InvalidEnvelope("JSON nesting limit exceeded")
            elif char in "]}":
                depth -= 1
        payload = json.loads(text, object_pairs_hook=_pairs, parse_int=_integer, parse_constant=_constant)
        todo = [payload]
        count = 0
        while todo:
            value = todo.pop()
            count += 1
            if count > MAX_NODES:
                raise InvalidEnvelope("JSON node limit exceeded")
            if isinstance(value, dict):
                todo.extend(value.keys())
                todo.extend(value.values())
            elif isinstance(value, list):
                todo.extend(value)
            elif isinstance(value, str):
                value.encode("utf-8", "strict")  # reject lone surrogate escapes
            elif isinstance(value, float) and not math.isfinite(value):
                raise InvalidEnvelope("non-finite JSON number")
        if not isinstance(payload, dict) or set(payload) - {"method", "params"}:
            raise InvalidEnvelope("expected method/params object")
        method = payload.get("method")
        if not isinstance(method, str) or not 1 <= len(method) <= 128:
            raise InvalidEnvelope("method must be a nonempty bounded string")
        params = payload.get("params", {})
        if not isinstance(params, dict):
            raise InvalidEnvelope("params must be an object")
        return method, params
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InvalidEnvelope("invalid UTF-8 JSON") from exc
