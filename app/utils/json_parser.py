"""Extract a single JSON object from raw model text."""

import json
import re
from typing import Any

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


class JSONExtractionError(ValueError):
    pass


def _first_balanced_object(text: str) -> str | None:
    """Return the first balanced {...} span, honouring JSON string quoting."""
    start = text.find("{")
    while start != -1:
        depth, in_str, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def extract_json_text(raw: str) -> str:
    """Strip whitespace / a markdown fence / surrounding prose; return the JSON object text.

    Raises JSONExtractionError if no object-like span exists. Does not validate the JSON.
    """
    text = (raw or "").strip()
    if not text:
        raise JSONExtractionError("empty response")
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    span = _first_balanced_object(text)
    if span is None:
        raise JSONExtractionError("no JSON object found in response")
    return span


_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def partial_string_field(text: str, field: str) -> str | None:
    """Best-effort value of a top-level string field in a JSON object that is still streaming.

    Returns the decoded text received so far (an unfinished escape at the end is dropped), or
    None if the field has not started. Used only for live display; the final reply is always
    fully parsed and validated.
    """
    m = re.search(r'"' + re.escape(field) + r'"\s*:\s*"', text)
    if not m:
        return None
    out: list[str] = []
    i, n = m.end(), len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            break
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            break
        nxt = text[i + 1]
        if nxt == "u":
            hex_digits = text[i + 2 : i + 6]
            if len(hex_digits) < 4 or not all(c in "0123456789abcdefABCDEF" for c in hex_digits):
                break
            out.append(chr(int(hex_digits, 16)))
            i += 6
        else:
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
    return "".join(out)


def parse_json_object(raw: str) -> dict[str, Any]:
    text = extract_json_text(raw)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JSONExtractionError(f"invalid JSON: {exc.msg} (line {exc.lineno})") from exc
    if not isinstance(value, dict):
        raise JSONExtractionError("expected a JSON object")
    return value
