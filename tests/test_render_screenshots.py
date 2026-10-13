import json
import re

import pytest

from tools import render_screenshots
from tools.terminal_svg import render


@pytest.fixture(params=["en", "de"])
def transcript(request, tmp_path):
    config_path = tmp_path / "claude_desktop_config.json"
    config_path.write_text(json.dumps(render_screenshots.LEGACY_CONFIG), encoding="utf-8")
    raw = render_screenshots.simulate(request.param, config_path)
    return request.param, render_screenshots.anonymize(raw, config_path), tmp_path


def test_screenshot_transcript_contains_no_private_data(transcript):
    _, lines, tmp_path = transcript
    text = "\n".join(line + (answer or "") for _, line, answer in lines)

    render_screenshots.assert_no_private_data(lines)
    assert str(tmp_path) not in text
    assert not re.search(r"EAA[A-Za-z0-9]{20,}", text)


def test_leak_check_rejects_token_like_strings():
    with pytest.raises(RuntimeError):
        render_screenshots.assert_no_private_data([("say", "token EAA" + "x" * 30, None)])


def test_transcript_splits_into_three_shots(transcript):
    lang, lines, _ = transcript

    names = [name for name, part in render_screenshots.split(lines, lang) if part]

    assert names == ["01-install", "02-credentials", "03-claude"]


def test_svg_escapes_markup():
    svg = render([("say", "<script>&", None), ("ask", "[?]  Q: ", "<b>")], "title")

    assert "<script>" not in svg
    assert "&lt;script&gt;&amp;" in svg
    assert "&lt;b&gt;" in svg
