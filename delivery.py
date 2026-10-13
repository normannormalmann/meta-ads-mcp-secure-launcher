"""Derives an Ads-Manager-like delivery status for campaigns (upstream #58, #34).

The Graph API has no single "delivery" field: `status` is what the user set,
`effective_status` includes parent states, and campaigns that simply ran past
their end date stay ACTIVE. This combines effective_status, start/stop time and
the ad sets' end_time/effective_status.
"""
from collections import Counter
from dataclasses import dataclass

from graph_client import parse_graph_time as _parse_time

PASSTHROUGH_STATUSES = ("DELETED", "ARCHIVED", "PAUSED", "WITH_ISSUES", "IN_PROCESS")
CAMPAIGN_FIELDS = (
    "id,name,objective,status,configured_status,effective_status,start_time,stop_time,"
    "daily_budget,lifetime_budget,adsets.limit(100){end_time,effective_status}"
)


@dataclass(frozen=True)
class Delivery:
    status: str
    reason: str


def _ad_set_delivery(ad_sets, now):
    if not ad_sets:
        return Delivery("NOT_DELIVERING", "Campaign has no ad sets.")
    end_times = [_parse_time(ad_set.get("end_time")) for ad_set in ad_sets]
    if all(end and end <= now for end in end_times):
        return Delivery("COMPLETED", "All ad sets have reached their end time.")
    if not any(ad_set.get("effective_status") == "ACTIVE" for ad_set in ad_sets):
        return Delivery("NOT_DELIVERING", "No ad set is active.")
    return None


def classify(campaign, now):
    effective = str(campaign.get("effective_status") or campaign.get("status") or "").upper()
    if effective in PASSTHROUGH_STATUSES:
        return Delivery(effective, f"effective_status is {effective}.")
    stop, start = _parse_time(campaign.get("stop_time")), _parse_time(campaign.get("start_time"))
    if stop and stop <= now:
        return Delivery("COMPLETED", "Campaign stop_time has passed.")
    if start and start > now:
        return Delivery("SCHEDULED", "Campaign start_time is in the future.")
    if "adsets" in campaign:
        if campaign["adsets"].get("paging", {}).get("next"):
            return Delivery("ACTIVE", "Active and within its schedule; ad set list incomplete (over 100 ad sets), "
                                       "so ad set end times were not evaluated.")
        ad_set_result = _ad_set_delivery(campaign["adsets"].get("data", []), now)
        if ad_set_result:
            return ad_set_result
    return Delivery("ACTIVE", "Active and within its schedule (budget, billing and ad review are not checked).")


def summarize(campaigns, now, only=None):
    rows = []
    for campaign in campaigns:
        result = classify(campaign, now)
        row = {key: value for key, value in campaign.items() if key != "adsets"}
        rows.append({**row, "delivery": result.status, "delivery_reason": result.reason})
    counts = Counter(row["delivery"] for row in rows)
    shown = [row for row in rows if not only or row["delivery"] == only.upper()]
    return {"counts": dict(sorted(counts.items())), "campaigns": shown}
