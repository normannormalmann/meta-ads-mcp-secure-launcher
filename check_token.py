"""Shows validity, expiry and permissions of the stored Meta access token.

Run: venv\\Scripts\\python.exe check_token.py [--app-id ID] [--lang de|en]
The App ID is taken from --app-id or the META_APP_ID environment variable.
Never prints the token or app secret. Exit code 1 if there are warnings,
2 on errors, so it can also run as a scheduled task.
"""
import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from graph_client import GRAPH_BASE_URL
from i18n import add_language_argument, detect_language, t
from launch_secure import load_secrets

GRAPH_DEBUG_URL = f"{GRAPH_BASE_URL}/debug_token"
WARN_DAYS = 14
SECONDS_PER_DAY = 86_400
REQUEST_TIMEOUT = 15.0
EXPECTED_SCOPES = frozenset({
    "ads_management", "ads_read", "business_management", "public_profile",
    # page/Instagram identity for ad creatives (get_account_pages, create_ad_creative)
    "pages_show_list", "pages_read_engagement", "pages_manage_ads", "instagram_basic",
})


@dataclass
class Report:
    lines: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _expiry_line(label, timestamp, now, lang, report):
    if not timestamp:
        report.lines.append(t(lang, "token_never", label=label))
        return
    days = int((timestamp - now) // SECONDS_PER_DAY)
    date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    report.lines.append(t(lang, "token_expiry", label=label, date=date, days=days))
    if days < WARN_DAYS:
        report.warnings.append(t(lang, "token_expiring", label=label, days=days))


def assess(data, now, lang):
    report = Report()
    is_valid = bool(data.get("is_valid"))
    report.lines.append(t(lang, "token_valid", value=t(lang, "yes" if is_valid else "no")))
    report.lines.append(t(lang, "token_type", type=data.get("type", "?"), application=data.get("application", "?")))
    if not is_valid:
        report.warnings.append(t(lang, "token_invalid"))
    _expiry_line(t(lang, "label_expiry"), data.get("expires_at"), now, lang, report)
    _expiry_line(t(lang, "label_data_access"), data.get("data_access_expires_at"), now, lang, report)

    scopes = sorted(data.get("scopes", []))
    report.lines.append(t(lang, "token_scopes", scopes=", ".join(scopes) or "-"))
    extra = [scope for scope in scopes if scope not in EXPECTED_SCOPES]
    if extra:
        report.warnings.append(t(lang, "token_extra_scopes", scopes=", ".join(extra)))
    return report


def resolve_app_id(argument, environ):
    return argument or environ.get("META_APP_ID") or None


def fetch_debug_data(app_id, secrets):
    params = {
        "input_token": secrets["META_ACCESS_TOKEN"],
        "access_token": f"{app_id}|{secrets['META_APP_SECRET']}",
    }
    response = httpx.get(GRAPH_DEBUG_URL, params=params, timeout=REQUEST_TIMEOUT)
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "unknown error"))
    return payload["data"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the stored Meta access token.")
    add_language_argument(parser)
    parser.add_argument("--app-id", help="Meta App ID (default: META_APP_ID)")
    args = parser.parse_args(argv)
    lang = detect_language(args.lang)

    app_id = resolve_app_id(args.app_id, os.environ)
    if not app_id:
        print(t(lang, "token_missing_app_id"), file=sys.stderr)
        return 2
    try:
        data = fetch_debug_data(app_id, load_secrets())
    except httpx.HTTPError as err:
        # str(err) may contain the request URL incl. token and secret
        print(t(lang, "token_network_error", kind=type(err).__name__), file=sys.stderr)
        return 2
    except (RuntimeError, KeyError, ValueError) as err:
        print(t(lang, "token_failed", kind=type(err).__name__, detail=err), file=sys.stderr)
        return 2

    report = assess(data, time.time(), lang)
    for line in report.lines:
        print(line)
    for warning in report.warnings:
        print(t(lang, "token_warning_prefix") + warning)
    return 1 if report.warnings else 0


if __name__ == "__main__":
    sys.exit(main())
