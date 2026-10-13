import re

import pytest

import i18n


def test_all_languages_define_the_same_keys():
    assert set(i18n.MESSAGES["de"]) == set(i18n.MESSAGES["en"])


def test_placeholders_match_between_languages():
    for key, english in i18n.MESSAGES["en"].items():
        german = i18n.MESSAGES["de"][key]
        assert set(re.findall(r"\{(\w+)\}", english)) == set(re.findall(r"\{(\w+)\}", german)), key


@pytest.mark.parametrize(
    "requested, environ, system_locale, expected",
    [
        ("de", {}, "en_US", "de"),
        (None, {"META_MCP_LANG": "en"}, "de_DE", "en"),
        (None, {}, "German_Germany", "de"),
        (None, {}, "de_AT", "de"),
        (None, {}, "en_GB", "en"),
        (None, {}, "", "en"),
        ("fr", {}, "fr_FR", "en"),
    ],
)
def test_detect_language(requested, environ, system_locale, expected):
    assert i18n.detect_language(requested, environ, system_locale) == expected


def test_t_formats_values():
    assert i18n.t("en", "step", current=1, total=7, title="X") == "Step 1/7: X"
