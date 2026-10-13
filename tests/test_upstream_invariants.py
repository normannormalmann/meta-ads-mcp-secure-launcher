"""Fails when a meta-ads-mcp update changes assumptions the launcher relies on."""
import ast

import tool_policy
from tests import upstream_scan


def test_every_upstream_tool_is_classified():
    upstream = upstream_scan.registered_tool_names()

    unclassified = upstream - set(tool_policy.TOOL_CATEGORIES)
    stale = set(tool_policy.TOOL_CATEGORIES) - upstream

    assert not unclassified, f"classify new upstream tools in tool_policy.TOOL_CATEGORIES: {sorted(unclassified)}"
    assert not stale, f"remove tools that no longer exist upstream: {sorted(stale)}"


def test_upstream_uses_only_httpx_except_read_only_token_exchange():
    # write_guard hooks httpx; any other HTTP client would bypass it
    assert upstream_scan.http_imports() == {("core/auth.py", "requests")}
    assert upstream_scan.attribute_calls("core/auth.py", "requests") == {"get"}


def test_scanner_finds_decorated_and_called_registrations(tmp_path):
    (tmp_path / "mod.py").write_text(
        "@mcp_server.tool()\nasync def a(): pass\n"
        "def b(): pass\n"
        "b = mcp_server.tool()(b)\n"
        "@other.tool()\ndef c(): pass\n",
        encoding="utf-8",
    )

    assert upstream_scan.registered_tool_names(tmp_path) == {"a", "b"}


def test_scanner_finds_other_http_libraries(tmp_path):
    (tmp_path / "mod.py").write_text("import aiohttp\nfrom urllib.request import urlopen\nimport httpx\n", encoding="utf-8")

    assert upstream_scan.http_imports(tmp_path) == {("mod.py", "aiohttp"), ("mod.py", "urllib.request")}


def test_scan_does_not_import_the_package():
    source = (upstream_scan.package_dir() / "core" / "utils.py").read_text(encoding="utf-8")

    assert "setup_logging" in source  # parsed as text; importing would write to the real log
    assert ast.parse(source)
