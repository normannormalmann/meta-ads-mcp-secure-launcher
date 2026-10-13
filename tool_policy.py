"""MCP tool annotations and result-size metadata for the meta-ads-mcp tools.

Upstream declares neither, so clients cannot tell read-only tools from tools
that change live campaigns. Every upstream tool must be listed here; the test
suite fails when an update adds or removes one (tests/test_upstream_invariants.py).
Tools missing at runtime are treated as destructive.
"""
import logging

from mcp.types import ToolAnnotations

READ = "read"
ADD = "add"
MODIFY = "modify"
LOCAL_WRITE = "local_write"

CATEGORY_ANNOTATIONS = {
    READ: ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True),
    ADD: ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    MODIFY: ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    LOCAL_WRITE: ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
}

TOOL_CATEGORIES = {
    # reading accounts, objects, insights and search endpoints
    **dict.fromkeys((
        "get_account_info", "get_account_pages", "get_ad_accounts", "get_ad_creatives", "get_ad_details",
        "get_ad_image", "get_ad_video", "get_ads", "get_adset_details", "get_adsets", "get_campaign_details",
        "get_campaigns", "get_creative_details", "get_image_by_hash", "get_insights", "get_interest_suggestions",
        "get_login_link", "search_ads_archive", "search_behaviors", "search_demographics", "search_geo_locations",
        "search_interests", "search_pages_by_name", "estimate_audience_size", "compute_image_crops",
        "generate_report",
    ), READ),
    # creating new objects; existing ones stay untouched
    **dict.fromkeys((
        "create_ad", "create_adset", "create_campaign", "create_ad_creative", "upload_ad_image",
        "duplicate_ad", "duplicate_adset", "duplicate_campaign", "duplicate_creative",
    ), ADD),
    # changing live objects or their spend
    **dict.fromkeys((
        "update_ad", "update_ad_creative", "update_adset", "update_campaign", "create_budget_schedule",
    ), MODIFY),
    "save_ad_image_locally": LOCAL_WRITE,
}

# tools added by the launcher itself (extensions.py)
LAUNCHER_TOOL_CATEGORIES = {
    "get_campaign_delivery": READ,
    # instagram.py, only registered with META_ENABLE_INSTAGRAM_INSIGHTS=1
    "get_instagram_accounts": READ,
    "get_instagram_reels": READ,
    "get_instagram_reel_insights": READ,
    "get_instagram_public_media": READ,
}

MAX_RESULT_SIZE_KEY = "anthropic/maxResultSizeChars"
MAX_RESULT_SIZE_CEILING = 500_000  # hard ceiling documented for Claude Code
RESULT_SIZE_LIMITS = {
    "get_insights": 500_000,
    "get_ads": 250_000,
    "get_adsets": 150_000,
    "get_campaigns": 100_000,
    "get_campaign_delivery": 100_000,
    "get_instagram_reels": 100_000,
    "get_instagram_public_media": 100_000,
}

logger = logging.getLogger("meta-ads-mcp.tool-policy")


def apply(server):
    """Annotates all registered tools of a FastMCP server; returns names of unknown tools."""
    unknown = []
    categories = {**TOOL_CATEGORIES, **LAUNCHER_TOOL_CATEGORIES}
    for tool in server._tool_manager.list_tools():
        category = categories.get(tool.name)
        if category is None:
            unknown.append(tool.name)
            category = MODIFY
        tool.annotations = CATEGORY_ANNOTATIONS[category].model_copy()
        if tool.name in RESULT_SIZE_LIMITS:
            tool.meta = {**(tool.meta or {}), MAX_RESULT_SIZE_KEY: RESULT_SIZE_LIMITS[tool.name]}
    if unknown:
        logger.warning("Unclassified tools treated as destructive: %s", ", ".join(sorted(unknown)))
    return sorted(unknown)
