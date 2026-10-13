"""Minimal async client for the Meta Graph API, used by the launcher's own tools.

Independent of meta-ads-mcp internals. Uses httpx, so every request passes the
write guard. Errors never contain the request URL, because it carries the token.
"""
import hashlib
import hmac
from datetime import datetime

import httpx

GRAPH_API_VERSION = "v24.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
REQUEST_TIMEOUT = 30.0
PAGE_SIZE = 100
MAX_PAGES = 20
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"  # e.g. 2026-10-01T10:00:00+0000


def parse_graph_time(value):
    """Parses a Graph API timestamp; None for missing or malformed values."""
    try:
        return datetime.strptime(value, TIME_FORMAT) if value else None
    except ValueError:
        return None


class GraphError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class GraphClient:
    def __init__(self, access_token, app_secret=None, transport=None):
        self._access_token = access_token
        self._app_secret = app_secret
        self._transport = transport

    @classmethod
    def from_env(cls, environ):
        token = environ.get("META_ACCESS_TOKEN")
        if not token:
            raise GraphError("META_ACCESS_TOKEN is not available - start the server via launch_secure.py.")
        return cls(token, environ.get("META_APP_SECRET"))

    def _auth_params(self):
        params = {"access_token": self._access_token}
        if self._app_secret:
            params["appsecret_proof"] = hmac.new(
                self._app_secret.encode(), self._access_token.encode(), hashlib.sha256
            ).hexdigest()
        return params

    async def get(self, path, params=None):
        query = {**(params or {}), **self._auth_params()}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(f"{GRAPH_BASE_URL}/{path.lstrip('/')}", params=query)
        except httpx.HTTPError as err:
            raise GraphError(f"Network error ({type(err).__name__})") from None
        try:
            payload = response.json()
        except ValueError:
            raise GraphError(f"Unexpected response from Graph API (HTTP {response.status_code})") from None
        if "error" in payload:
            error = payload["error"]
            raise GraphError(error.get("message", "Unknown Graph API error"), error.get("code"))
        return payload

    async def iter_pages(self, path, params=None, page_size=PAGE_SIZE):
        """Yields the data list of each page, following cursor paging up to MAX_PAGES."""
        after = None
        for _ in range(MAX_PAGES):
            page_params = {**(params or {}), "limit": page_size, **({"after": after} if after else {})}
            payload = await self.get(path, page_params)
            yield payload.get("data", [])
            paging = payload.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not paging.get("next") or not after:
                return

    async def get_all(self, path, params=None, max_items=None):
        """Collects items across pages; stops at max_items or MAX_PAGES."""
        items = []
        page_size = min(PAGE_SIZE, max_items) if max_items else PAGE_SIZE
        async for page in self.iter_pages(path, params, page_size):
            items.extend(page)
            if max_items and len(items) >= max_items:
                return items[:max_items]
        return items
