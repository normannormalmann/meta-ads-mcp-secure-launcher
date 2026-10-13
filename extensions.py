"""Tools and server instructions the launcher adds to meta-ads-mcp.

Registered on the upstream FastMCP server before it starts. The tools use the
launcher's own graph_client, not meta-ads-mcp internals.
"""
import os
from datetime import datetime, timezone

import delivery
import instagram
import settings
from graph_client import GraphClient, GraphError
from tool_output import clamp, error_text, json_text

DEFAULT_CAMPAIGN_LIMIT = 100
MAX_CAMPAIGN_LIMIT = 500
DELIVERY_PAGE_SIZE = 25  # each campaign carries up to 100 nested ad sets
TOOL_NAMES = ("get_campaign_delivery",)

DELIVERY_DESCRIPTION = """Get campaigns with a computed delivery status like the "Delivery" column in Meta Ads Manager.

Unlike get_campaigns, this also catches campaigns that still say ACTIVE although they ended:
it combines effective_status, start_time/stop_time and the ad sets' end_time/effective_status.

Delivery values: ACTIVE, SCHEDULED, COMPLETED, NOT_DELIVERING, PAUSED, WITH_ISSUES,
IN_PROCESS, ARCHIVED, DELETED. ACTIVE does not check budget, billing or ad review.

Args:
    account_id: Ad account ID (act_123...). Optional if a default account is configured.
    delivery_filter: Only return campaigns with this delivery value, e.g. "ACTIVE".
    limit: Maximum number of campaigns to read (default 100, max 500).
"""


def make_campaign_delivery_tool(client_factory, default_account, clock):
    async def get_campaign_delivery(account_id: str = "", delivery_filter: str = "", limit: int = DEFAULT_CAMPAIGN_LIMIT) -> str:
        try:
            account = settings.parse_account_id(account_id) or default_account
        except ValueError:
            return error_text(f"Invalid account_id {account_id!r}; expected act_<digits>.")
        if not account:
            return error_text("No account_id given and no default account configured.")
        try:
            max_items = clamp(limit, MAX_CAMPAIGN_LIMIT)
        except (TypeError, ValueError):
            return error_text(f"Invalid limit {limit!r}; expected a whole number up to {MAX_CAMPAIGN_LIMIT}.")
        try:
            campaigns = await client_factory().get_all(f"{account}/campaigns", {"fields": delivery.CAMPAIGN_FIELDS},
                                                       max_items=max_items + 1, page_size=DELIVERY_PAGE_SIZE)
        except GraphError as err:
            return error_text(str(err))
        truncated, campaigns = len(campaigns) > max_items, campaigns[:max_items]
        summary = delivery.summarize(campaigns, clock(), only=delivery_filter or None)
        return json_text({"account_id": account, "generated_at": clock().isoformat(),
                          "fetched": len(campaigns), "truncated": truncated, **summary})

    return get_campaign_delivery


def _instructions(existing, default_account):
    hint = (f"Default Meta ad account: {default_account}. Use it whenever a tool needs an account_id "
            "and the user has not named another account.")
    return f"{existing}\n\n{hint}" if existing else hint


def register(server, environ=None, client_factory=None, clock=None):
    """Adds the launcher tools (Instagram tools only if enabled) and default-account instructions."""
    environ = os.environ if environ is None else environ
    default_account = settings.parse_account_id(environ.get(settings.ENV_DEFAULT_ACCOUNT))
    client_factory = client_factory or (lambda: GraphClient.from_env(environ))
    clock = clock or (lambda: datetime.now(timezone.utc))

    server.add_tool(make_campaign_delivery_tool(client_factory, default_account, clock),
                    name="get_campaign_delivery", description=DELIVERY_DESCRIPTION)
    if settings.is_enabled(environ.get(settings.ENV_ENABLE_INSTAGRAM)):
        instagram.register(server, client_factory, settings.parse_ig_user_id(environ.get(settings.ENV_DEFAULT_IG_USER)))
    if default_account:
        low_level = server._mcp_server
        low_level.instructions = _instructions(low_level.instructions, default_account)
