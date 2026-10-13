"""Destination-URL allowlist for ad creatives (used by write_guard).

Deny by default: every URL-looking string in a write request - at the top level
or nested in JSON specs such as object_story_spec - counts as a destination,
except keys that only hold media or display text. Hosts must be plain ASCII
domain names on the allowlist; anything a browser might parse differently
(backslashes, whitespace, non-ASCII, ports, user info, IP addresses) is refused.
"""
import ipaddress
import re
from urllib.parse import urlsplit

import form_fields

ENV_ALLOWED_DOMAINS = "META_GUARD_ALLOWED_URL_DOMAINS"
DESTINATION_KEYS = frozenset({"link", "website_url", "template_url", "deeplink_url", "url", "object_url",
                              "app_link", "link_deep_link_url", "object_store_url"})
NON_DESTINATION_KEYS = frozenset({"display_url", "url_tags", "thumbnail_url", "image_url", "picture"})
SPEC_FIELDS = frozenset({"object_story_spec", "asset_feed_spec", "template_url_spec", "creative_features_spec",
                         "dynamic_creative_spec", "promoted_object", "reminder_data"})
URL_PREFIXES = ("http://", "https://", "//", "javascript:", "data:", "vbscript:")
DOMAIN_PATTERN = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", re.ASCII)
SAFE_URL_CHARS = re.compile(r"[!-~]+", re.ASCII)  # printable ASCII without spaces
HTTPS_PORT = 443


def parse_domains(raw):
    """Parses 'a.com, b.org' into a frozenset; raises ValueError naming the variable."""
    if raw is None or not raw.strip():
        return frozenset()
    domains = [part.strip().lower().rstrip(".") for part in raw.split(",")]
    invalid = [domain for domain in domains if not DOMAIN_PATTERN.fullmatch(domain)]
    if invalid or not domains:
        raise ValueError(f"{ENV_ALLOWED_DOMAINS} must be a comma-separated list of domains like 'example.com', got {raw!r}.")
    return frozenset(domains)


def _is_destination(key, value):
    if not isinstance(value, str) or key in NON_DESTINATION_KEYS:
        return False
    return key in DESTINATION_KEYS or value.strip().lower().startswith(URL_PREFIXES)


def destinations_in_pairs(pairs):
    """Destination URLs in top-level fields and in JSON-encoded field values."""
    return [value for key, value in form_fields.all_pairs(pairs, strict_keys=SPEC_FIELDS) if _is_destination(key, value)]


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def is_allowed(url, domains):
    if not SAFE_URL_CHARS.fullmatch(url) or "\\" in url:
        return False
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, HTTPS_PORT):
        return False
    host = (parts.hostname or "").rstrip(".")
    if not DOMAIN_PATTERN.fullmatch(host) or _is_ip(host):
        return False
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def check_destinations(pairs, domains):
    """Returns a block reason or None. No-op when no domains are configured."""
    if not domains:
        return None
    try:
        urls = destinations_in_pairs(pairs)
    except form_fields.Unparseable as err:
        return f"Field '{err}' could not be parsed to check destination URLs - blocked for safety."
    for url in urls:
        if not is_allowed(url, domains):
            return (f"Ad destination '{url[:120]}' is not on the allowed list ({', '.join(sorted(domains))}). "
                    f"Only https links to these domains are allowed; change {ENV_ALLOWED_DOMAINS} to extend it.")
    return None
