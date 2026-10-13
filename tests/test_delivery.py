from datetime import datetime, timezone

import pytest

import delivery

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
PAST = "2026-09-01T00:00:00+0000"
FUTURE = "2026-11-01T00:00:00+0000"


def campaign(**fields):
    base = {"id": "1", "name": "C", "status": "ACTIVE", "configured_status": "ACTIVE", "effective_status": "ACTIVE"}
    return {**base, **fields}


def adsets(*entries):
    return {"data": [dict(entry) for entry in entries]}


@pytest.mark.parametrize("fields, expected", [
    ({}, "ACTIVE"),
    ({"stop_time": PAST}, "COMPLETED"),
    ({"stop_time": FUTURE}, "ACTIVE"),
    ({"start_time": FUTURE}, "SCHEDULED"),
    ({"effective_status": "PAUSED", "configured_status": "PAUSED"}, "PAUSED"),
    ({"effective_status": "WITH_ISSUES"}, "WITH_ISSUES"),
    ({"effective_status": "IN_PROCESS"}, "IN_PROCESS"),
    ({"effective_status": "ARCHIVED"}, "ARCHIVED"),
    ({"effective_status": "DELETED"}, "DELETED"),
    ({"effective_status": "PAUSED", "stop_time": PAST}, "PAUSED"),
])
def test_campaign_level_status(fields, expected):
    assert delivery.classify(campaign(**fields), NOW).status == expected


def test_completed_when_all_ad_sets_ended():
    entry = campaign(adsets=adsets({"end_time": PAST, "effective_status": "ACTIVE"}, {"end_time": PAST, "effective_status": "PAUSED"}))

    result = delivery.classify(entry, NOW)

    assert result.status == "COMPLETED"
    assert "ad sets" in result.reason


def test_active_when_one_ad_set_has_no_end_time():
    entry = campaign(adsets=adsets({"end_time": PAST, "effective_status": "ACTIVE"}, {"effective_status": "ACTIVE"}))

    assert delivery.classify(entry, NOW).status == "ACTIVE"


def test_not_delivering_when_no_ad_set_is_active():
    entry = campaign(adsets=adsets({"effective_status": "PAUSED"}, {"effective_status": "CAMPAIGN_PAUSED"}))

    assert delivery.classify(entry, NOW).status == "NOT_DELIVERING"


def test_campaign_without_ad_sets_is_not_delivering():
    assert delivery.classify(campaign(adsets=adsets()), NOW).status == "NOT_DELIVERING"


def test_unparseable_time_is_ignored():
    assert delivery.classify(campaign(stop_time="soon"), NOW).status == "ACTIVE"


def test_summary_counts_and_drops_nested_ad_sets():
    campaigns = [campaign(id="1"), campaign(id="2", stop_time=PAST), campaign(id="3", stop_time=PAST,
                 adsets=adsets({"end_time": PAST, "effective_status": "ACTIVE"}))]

    summary = delivery.summarize(campaigns, NOW)

    assert summary["counts"] == {"ACTIVE": 1, "COMPLETED": 2}
    assert [c["delivery"] for c in summary["campaigns"]] == ["ACTIVE", "COMPLETED", "COMPLETED"]
    assert all("adsets" not in c for c in summary["campaigns"])
    assert summary["campaigns"][1]["delivery_reason"]


def test_summary_filters_by_status():
    campaigns = [campaign(id="1"), campaign(id="2", stop_time=PAST)]

    summary = delivery.summarize(campaigns, NOW, only="ACTIVE")

    assert [c["id"] for c in summary["campaigns"]] == ["1"]
    assert summary["counts"] == {"ACTIVE": 1, "COMPLETED": 1}


def test_incomplete_ad_set_list_is_not_used_for_conclusions():
    entry = campaign(adsets={"data": [{"end_time": PAST, "effective_status": "PAUSED"}], "paging": {"next": "https://x"}})

    result = delivery.classify(entry, NOW)

    assert result.status == "ACTIVE"
    assert "incomplete" in result.reason
