"""Read-only Instagram tools for organic Reels (opt-in via META_ENABLE_INSTAGRAM_INSIGHTS).

Instagram Graph API with Facebook Login on graph.facebook.com. Needs a
Business/Creator account linked to a Facebook Page and the scopes
instagram_basic, instagram_manage_insights, pages_read_engagement, pages_show_list.
Insights are organic only (ad interactions are not counted), can be delayed up
to 48 h and are kept up to 2 years. Media in albums have no insights.
"""
import json
import re
from datetime import datetime, timezone

from graph_client import GraphError, parse_graph_time

REEL_METRICS = ("reach", "views", "likes", "comments", "shares", "saved", "total_interactions",
                "ig_reels_avg_watch_time", "reposts")
MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,thumbnail_url"
PUBLIC_MEDIA_FIELDS = "id,caption,like_count,comments_count,view_count,permalink,timestamp,media_product_type"
DEFAULT_REELS_LIMIT = 25
MAX_REELS_LIMIT = 100
DEFAULT_PUBLIC_LIMIT = 12
MAX_PUBLIC_LIMIT = 50
NUMERIC_ID = re.compile(r"\d{5,25}")
USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")  # Instagram username charset; blocks field-expansion injection
TOOL_NAMES = ("get_instagram_accounts", "get_instagram_reels", "get_instagram_reel_insights", "get_instagram_public_media")
ORGANIC_NOTE = ("Organic values only: interactions from ads are not counted. Data can be delayed up to 48 hours; "
                "missing metrics are null, not 0. ig_reels_avg_watch_time is in milliseconds.")


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


def _error(message):
    return _json({"error": message})


def _clamp(value, maximum):
    return max(1, min(int(value), maximum))


def make_accounts_tool(client_factory):
    async def get_instagram_accounts() -> str:
        """List Instagram Business/Creator accounts linked to your Facebook Pages (needed for the ig_user_id)."""
        try:
            pages = await client_factory().get_all("me/accounts", {"fields": "id,name,instagram_business_account{id,username,name}"})
        except GraphError as err:
            return _error(str(err))
        accounts = [
            {"page_id": page["id"], "page_name": page.get("name"), "ig_user_id": page["instagram_business_account"]["id"],
             "ig_username": page["instagram_business_account"].get("username")}
            for page in pages if page.get("instagram_business_account")
        ]
        return _json({"accounts": accounts})

    return get_instagram_accounts


def _resolve_ig_user(ig_user_id, default):
    candidate = (ig_user_id or "").strip() or default
    if not candidate:
        return None, "No ig_user_id given and no default configured - call get_instagram_accounts first."
    if not NUMERIC_ID.fullmatch(candidate):
        return None, f"Invalid ig_user_id {candidate!r}; expected the numeric Instagram user ID."
    return candidate, None


def _parse_since(since):
    if not since:
        return None
    try:
        return datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"Invalid since {since!r}; expected YYYY-MM-DD.") from None


def make_reels_tool(client_factory, default_ig_user):
    async def get_instagram_reels(ig_user_id: str = "", limit: int = DEFAULT_REELS_LIMIT, since: str = "") -> str:
        """List organic Reels of an Instagram Business/Creator account, newest first.

        Args:
            ig_user_id: Numeric Instagram user ID (optional if a default is configured; see get_instagram_accounts).
            limit: Maximum number of Reels (default 25, max 100).
            since: Only Reels published on or after this date (YYYY-MM-DD).
        """
        ig_user, problem = _resolve_ig_user(ig_user_id, default_ig_user)
        if problem:
            return _error(problem)
        try:
            cutoff = _parse_since(since)
        except ValueError as err:
            return _error(str(err))
        wanted, reels = _clamp(limit, MAX_REELS_LIMIT), []
        try:
            async for page in client_factory().iter_pages(f"{ig_user}/media", {"fields": MEDIA_FIELDS}):
                for item in page:
                    published = parse_graph_time(item.get("timestamp"))
                    if cutoff and published and published < cutoff:
                        return _json({"ig_user_id": ig_user, "reels": reels})
                    if item.get("media_product_type") == "REELS":
                        reels.append(item)
                        if len(reels) >= wanted:
                            return _json({"ig_user_id": ig_user, "reels": reels})
        except GraphError as err:
            return _error(str(err))
        return _json({"ig_user_id": ig_user, "reels": reels})

    return get_instagram_reels


def _metric_values(payload):
    values = {}
    for entry in payload.get("data", []):
        if "total_value" in entry:
            values[entry["name"]] = entry["total_value"].get("value")
        elif entry.get("values"):
            values[entry["name"]] = entry["values"][0].get("value")
    return values


async def _fetch_metrics(client, media_id):
    """All metrics in one call; if Meta rejects the set, retry one by one."""
    path = f"{media_id}/insights"
    try:
        return _metric_values(await client.get(path, {"metric": ",".join(REEL_METRICS)})), {}
    except GraphError as combined_error:
        values, errors = {}, {}
        for metric in REEL_METRICS:
            try:
                values.update(_metric_values(await client.get(path, {"metric": metric})))
            except GraphError as err:
                errors[metric] = str(err)
        if not values:
            raise combined_error
        return values, errors


def make_insights_tool(client_factory):
    async def get_instagram_reel_insights(media_id: str) -> str:
        """Organic performance of one Reel: reach, views, likes, comments, shares, saved,
        total_interactions, ig_reels_avg_watch_time (ms) and reposts. Ad interactions are not included.

        Args:
            media_id: Numeric ID of the Reel (from get_instagram_reels).
        """
        if not NUMERIC_ID.fullmatch((media_id or "").strip()):
            return _error(f"Invalid media_id {media_id!r}; expected the numeric media ID.")
        try:
            values, errors = await _fetch_metrics(client_factory(), media_id.strip())
        except GraphError as err:
            return _error(str(err))
        result = {"media_id": media_id.strip(), "metrics": {metric: values.get(metric) for metric in REEL_METRICS},
                  "note": ORGANIC_NOTE}
        if errors:
            result["metric_errors"] = errors
        return _json(result)

    return get_instagram_reel_insights


def make_public_media_tool(client_factory, default_ig_user):
    async def get_instagram_public_media(username: str, limit: int = DEFAULT_PUBLIC_LIMIT, only_reels: bool = True,
                                         ig_user_id: str = "") -> str:
        """Public posts of another Instagram Business/Creator account via Business Discovery.

        Returns likes, comments and views per post, but no insights (reach, saves, ...), which
        Meta only provides for your own accounts. Personal and age-restricted accounts are not available.

        Args:
            username: Instagram username of the other account (without @).
            limit: Number of recent posts to read (default 12, max 50).
            only_reels: Keep only Reels (default true).
            ig_user_id: Your own numeric Instagram user ID (optional if a default is configured).
        """
        if not USERNAME.fullmatch((username or "").strip().lstrip("@")):
            return _error(f"Invalid username {username!r}; use letters, digits, '.' and '_' only.")
        own_user, problem = _resolve_ig_user(ig_user_id, default_ig_user)
        if problem:
            return _error(problem)
        name, count = username.strip().lstrip("@"), _clamp(limit, MAX_PUBLIC_LIMIT)
        fields = (f"business_discovery.username({name}){{username,name,followers_count,media_count,"
                  f"media.limit({count}){{{PUBLIC_MEDIA_FIELDS}}}}}")
        try:
            payload = await client_factory().get(own_user, {"fields": fields})
        except GraphError as err:
            return _error(str(err))
        discovery = payload.get("business_discovery", {})
        items = discovery.get("media", {}).get("data", [])
        if only_reels:
            items = [item for item in items if item.get("media_product_type") == "REELS"]
        account = {key: value for key, value in discovery.items() if key != "media"}
        return _json({"account": account, "media": items})

    return get_instagram_public_media


def register(server, client_factory, default_ig_user):
    for factory in (make_accounts_tool(client_factory), make_reels_tool(client_factory, default_ig_user),
                    make_insights_tool(client_factory), make_public_media_tool(client_factory, default_ig_user)):
        server.add_tool(factory)
