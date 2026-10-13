"""Minimal async client for the Meta Graph API, used by the launcher's own tools.

Independent of meta-ads-mcp internals. Uses httpx, so every request passes the
write guard. Errors never contain the request URL, because it carries the token.
"""
import hashlib
import hmac

import httpx

GRAPH_API_VERSION = "v24.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
REQUEST_TIMEOUT = 30.0
PAGE_SIZE = 100
MAX_PAGES = 20


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

    async def get_all(self, path, params=None, max_items=None):
        """Follows cursor paging; stops at max_items or MAX_PAGES."""
        items, after = [], None
        page_size = min(PAGE_SIZE, max_items) if max_items else PAGE_SIZE
        for _ in range(MAX_PAGES):
            page_params = {**(params or {}), "limit": page_size, **({"after": after} if after else {})}
            payload = await self.get(path, page_params)
            items.extend(payload.get("data", []))
            if max_items and len(items) >= max_items:
                return items[:max_items]
            paging = payload.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not paging.get("next") or not after:
                break
        return items
