"""Reads every key/value a Graph API request carries, including nested JSON.

Shared by write_guard and url_policy. Duplicate keys are all kept (query and
body), because the guard must not check one value while Graph uses another.
"""
import json
from urllib.parse import parse_qsl

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


class Unparseable(ValueError):
    pass


def request_pairs(request):
    """(key, value) pairs from query and form body; None if the body format is unknown."""
    pairs = list(request.url.params.multi_items())
    body = request.read()
    if not body:
        return pairs
    if not request.headers.get("content-type", "").startswith(FORM_CONTENT_TYPE):
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return pairs + parse_qsl(text, keep_blank_values=True)


def looks_like_json(value):
    return isinstance(value, str) and value.lstrip()[:1] in ("{", "[")


def parse_json(value):
    """Parses a JSON-looking string; raises Unparseable (also for absurd nesting)."""
    try:
        return json.loads(value)
    except (json.JSONDecodeError, RecursionError) as err:
        raise Unparseable(str(err)) from None


def walk(value):
    """Yields (key, value) for every dict entry at any depth of a parsed JSON structure."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def all_pairs(pairs, strict_keys=()):
    """Top-level pairs plus every nested pair from JSON-encoded values.

    JSON-looking values that do not parse are treated as plain text (e.g. a
    campaign named "[Sale] Autumn"), except for keys in strict_keys, where they
    raise Unparseable so the caller can fail closed.
    """
    expanded = list(pairs)
    for key, value in pairs:
        if not looks_like_json(value):
            continue
        try:
            expanded.extend(walk(parse_json(value)))
        except Unparseable:
            if key in strict_keys:
                raise Unparseable(key) from None
    return expanded
