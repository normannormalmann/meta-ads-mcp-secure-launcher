"""Destination-URL allowlist for ad creatives (used by write_guard).

meta-ads-mcp sends nested creative specs (object_story_spec, asset_feed_spec, ...)
as JSON strings inside form fields. Every JSON-looking value is parsed and
searched recursively for destination keys. display_url (shown text only) and
url_tags (query parameters only) are not destinations and are ignored.
"""
import ipaddress
import json
import re
from urllib.parse import urlsplit

ENV_ALLOWED_DOMAINS = "META_GUARD_ALLOWED_URL_DOMAINS"
DESTINATION_KEYS = frozenset({"link", "website_url", "template_url", "deeplink_url"})
DOMAIN_PATTERN = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


class UnparseableField(ValueError):
    pass


def parse_domains(raw):
    """Parses 'a.com, b.org' into a frozenset; raises ValueError naming the variable."""
    if raw is None or not raw.strip():
        return frozenset()
    domains = [part.strip().lower().rstrip(".") for part in raw.split(",")]
    invalid = [domain for domain in domains if not DOMAIN_PATTERN.fullmatch(domain)]
    if invalid or not domains:
        raise ValueError(f"{ENV_ALLOWED_DOMAINS} must be a comma-separated list of domains like 'example.com', got {raw!r}.")
    return frozenset(domains)


def _looks_like_json(value):
    return value.lstrip()[:1] in ("{", "[")


def _destinations(value):
    """Yields destination strings found anywhere in a parsed JSON structure."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in DESTINATION_KEYS and isinstance(item, str):
                yield item
            else:
                yield from _destinations(item)
    elif isinstance(value, list):
        for item in value:
            yield from _destinations(item)


def destinations_in_fields(fields):
    """Destination URLs in top-level fields and JSON-encoded field values."""
    found = []
    for key, value in fields.items():
        if key in DESTINATION_KEYS:
            found.append(value)
        elif isinstance(value, str) and _looks_like_json(value):
            try:
                found.extend(_destinations(json.loads(value)))
            except json.JSONDecodeError as err:
                raise UnparseableField(key) from err
    return found


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def is_allowed(url, domains):
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or parts.username or parts.password or not parts.hostname:
        return False
    host = parts.hostname.lower().rstrip(".")
    if _is_ip(host):
        return False
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def check_destinations(fields, domains):
    """Returns a block reason or None. No-op when no domains are configured."""
    if not domains:
        return None
    try:
        urls = destinations_in_fields(fields)
    except UnparseableField as err:
        return f"Field '{err}' could not be parsed to check destination URLs - blocked for safety."
    for url in urls:
        if not is_allowed(url, domains):
            return (f"Ad destination '{url[:120]}' is not on the allowed list ({', '.join(sorted(domains))}). "
                    f"Only https links to these domains are allowed; change {ENV_ALLOWED_DOMAINS} to extend it.")
    return None
