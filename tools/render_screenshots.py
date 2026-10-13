"""Generates docs/screenshots/*.svg from a simulated wizard run.

Run from the repository root:  venv\\Scripts\\python.exe -m tools.render_screenshots

The real Wizard runs against ScriptedConsole/FakeSystem with placeholder data,
so nothing on this machine is touched. The output is checked for local paths,
user names and token-like strings before anything is written.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import check_token
import setup_wizard
from i18n import t
from tools.terminal_svg import render
from tools.wizard_fakes import FakeSystem, ScriptedConsole

REPO = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO / "docs" / "screenshots"
DISPLAY_ROOT = Path(r"C:\Users\you\meta-ads-mcp-secure-launcher")
DISPLAY_CONFIG = r"C:\Users\you\AppData\Roaming\Claude\claude_desktop_config.json"
PLACEHOLDER_APP_ID = "123456789012345"
NOW = datetime(2026, 10, 13, 9, 30, 0)
DAY = 86_400
SHOTS = (("01-install", 1, 3), ("02-credentials", 3, 6), ("03-claude", 6, None))
LEGACY_CONFIG = {
    "mcpServers": {
        "meta-ads-mcp": {"command": "meta-ads-mcp.exe", "env": {"META_APP_ID": PLACEHOLDER_APP_ID, "META_ACCESS_TOKEN": "placeholder"}},
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]},
    }
}
FAKE_TOKEN_DATA = {
    "is_valid": True,
    "type": "SYSTEM_USER",
    "application": "My Ads App",
    "expires_at": NOW.timestamp() + 59 * DAY + 3600,
    "data_access_expires_at": 0,
    "scopes": ["ads_management", "ads_read", "business_management"],
}


def fake_outputs(lang):
    secret_lines = [
        t(lang, "prompt_app_secret"),
        t(lang, "secret_ok", description=t(lang, "secret_received", length=32, tail="9f3a")),
        t(lang, "secret_saved", target="meta-ads-mcp/app_secret"),
        t(lang, "prompt_access_token"),
        t(lang, "secret_ok", description=t(lang, "secret_received", length=239, tail="Xk2Q")),
        t(lang, "secret_saved", target="meta-ads-mcp/access_token"),
    ]
    report = check_token.assess(FAKE_TOKEN_DATA, NOW.timestamp(), lang)
    return {
        "pip": ["Successfully installed meta-ads-mcp-1.0.128 mcp-1.28.1 httpx-0.28.1 pywin32-311 ..."],
        "pytest": ["90 passed in 0.62s"],
        "store_secrets --status": [t(lang, "status_missing", target=target) for target in ("meta-ads-mcp/app_secret", "meta-ads-mcp/access_token")],
        "store_secrets": secret_lines,
        "check_token": report.lines,
    }


def simulate(lang, config_path):
    yes = "j" if lang == "de" else "y"
    console = ScriptedConsole([PLACEHOLDER_APP_ID, yes, "", "", "", ""])
    system = FakeSystem(
        console,
        python=(3, 13, 1),
        existing={DISPLAY_ROOT / "requirements.lock"},
        results={"store_secrets --status": 1},
        outputs=fake_outputs(lang),
    )
    wizard = setup_wizard.Wizard(console, system, lang, config_path, root=DISPLAY_ROOT, now=lambda: NOW)
    if wizard.run() != setup_wizard.EXIT_OK:
        raise RuntimeError(f"simulated wizard run failed ({lang})")
    return console.transcript


def anonymize(transcript, config_path):
    replacements = {str(config_path.parent): str(Path(DISPLAY_CONFIG).parent)}

    def clean(text):
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    return [(kind, clean(text), answer) for kind, text, answer in transcript]


def assert_no_private_data(transcript):
    text = "\n".join(line + (answer or "") for _, line, answer in transcript)
    forbidden = [value for value in (os.environ.get("USERNAME"), os.environ.get("USERPROFILE"), tempfile.gettempdir()) if value]
    leaks = [value for value in forbidden if value.lower() in text.lower()]
    if leaks or re.search(r"EAA[A-Za-z0-9]{20,}", text):
        raise RuntimeError(f"screenshot transcript contains private data: {leaks or 'token-like string'}")


def split(transcript, lang):
    def heading_index(step):
        heading = t(lang, "step", current=step, total=setup_wizard.TOTAL_STEPS, title="")
        return next(i for i, (_, text, _) in enumerate(transcript) if text.startswith(heading))

    for name, start, end in SHOTS:
        first = 0 if start == 1 else heading_index(start)
        last = heading_index(end) if end else len(transcript)
        part = transcript[first:last]
        while part and not part[0][1]:
            part = part[1:]
        while part and not part[-1][1]:
            part = part[:-1]
        yield name, part


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for lang in ("en", "de"):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "claude_desktop_config.json"
            config_path.write_text(json.dumps(LEGACY_CONFIG), encoding="utf-8")
            transcript = anonymize(simulate(lang, config_path), config_path)
        assert_no_private_data(transcript)
        for name, part in split(transcript, lang):
            target = OUTPUT_DIR / f"{name}-{lang}.svg"
            target.write_text(render(part, "python setup_wizard.py"), encoding="utf-8")
            print(f"wrote {target.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
