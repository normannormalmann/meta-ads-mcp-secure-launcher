# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hardened launcher (Windows and macOS) around the upstream PyPI package `meta-ads-mcp` (Pipeboard, BSL 1.1). This repo contains **no upstream code** and must not vendor or patch it: everything works by importing the installed package and wrapping it at runtime. Modules are flat at the repo root (no package); tests import them via `sys.path` from `tests/conftest.py`.

## Commands

All commands run with the venv interpreter, Python 3.11–3.14 (`setup_wizard.MAX_PYTHON`: newer versions lack prebuilt wheels for pinned deps) (shown for Windows; on macOS use `venv/bin/python`):

```powershell
venv\Scripts\python.exe -m pip install -r requirements.lock      # pinned, audited deps (incl. meta-ads-mcp version)
venv\Scripts\python.exe -m pytest -q                              # full suite (also the install self-test)
venv\Scripts\python.exe -m pytest tests/test_write_guard.py -q    # one file
venv\Scripts\python.exe -m pytest tests/test_write_guard.py -k budget -q   # by name
venv\Scripts\python.exe -m tools.render_screenshots               # regenerate docs/screenshots/*.svg
venv\Scripts\python.exe launch_secure.py                          # run the server manually; startup errors go to stderr
```

Upgrading upstream: `pip install --upgrade meta-ads-mcp`, run pytest, then `pip freeze --exclude-editable > requirements.lock`, and check with `pip-audit -r requirements.lock`.

There is no linter/formatter config in the repo.

## Architecture

Startup order in `launch_secure.py:main()` matters and is deliberate:

1. `secure_logging.harden_logging()` runs **before** `meta_ads_mcp` is imported, so the package can't install its own unbounded/unmasked log handler. Afterwards `adopt_package_logger` takes over the `"meta-ads-mcp"` logger.
2. `write_guard.install(policy)` monkey-patches `httpx.AsyncClient.send`. Every upstream Graph/Pipeboard request goes through it. Blocked requests never leave the machine and return a Graph-style error response.
3. Config is validated (`settings.py`), then secrets are read via `credential_store.py` (`meta-ads-mcp/app_secret`, `meta-ads-mcp/access_token`) into env vars, and the upstream OAuth callback server and login-link tool are disabled through env flags.
4. `wire_upstream` imports `meta_ads_mcp.core.server.mcp_server` (FastMCP), registers launcher tools (`extensions.register`) and then annotates **all** tools (`tool_policy.apply`). Tool registration must happen before annotation.

Startup failures go through `_fail()`: one line on stderr and exit code 1. **stdout is reserved for the MCP stdio protocol**, so never print to it from launcher code.

Key modules:
- `write_guard.py`: request rules (DELETE, DELETED/ARCHIVED status, budget caps in cents = units × 100, budget schedules, path/override-param smuggling, destination allowlist via `url_policy.py`, Pipeboard host block). Fail-closed on unparseable bodies. `form_fields.py` extracts all key/value pairs, including duplicates and nested JSON.
- `tool_policy.py`: `TOOL_CATEGORIES` (upstream tools) and `LAUNCHER_TOOL_CATEGORIES` map each tool to read/add/modify/local_write → MCP annotations, plus `anthropic/maxResultSizeChars` metadata. At runtime, unknown tools default to destructive.
- `extensions.py` / `instagram.py` / `delivery.py`: launcher-owned tools. They use the launcher's own `graph_client.py` (httpx, so the write guard applies too), never upstream internals. Tools are built with factory functions (`make_*_tool(client_factory, ...)`, plus a `clock` where time matters) so tests can inject fakes. Instagram tools are only registered when `META_ENABLE_INSTAGRAM_INSIGHTS` is set.
- `credential_store.py`: platform switch on `sys.platform`. Windows uses pywin32 (`win32cred`). macOS calls `/usr/bin/security`: it reads with `find-generic-password -w` (exit 44 = not found) and writes through `security -i` with the command on stdin, so the secret never appears in argv. The write is verified by reading the value back. Secrets must match `[A-Za-z0-9]+` so the `security -i` parser can't be injected. Other platforms raise an error.
- `setup_wizard.py` and everything it imports (`settings.py`, `url_policy.py` → `form_fields.py`, `i18n.py`): **standard library only**, because the wizard runs before the venv exists. Platform differences in the wizard (venv interpreter path, Claude config location, message variants via `i18n.platform_text`) depend on `system.platform()`, so tests can fake macOS with `FakeSystem(platform_name="Darwin")`.
- `requirements.lock` marks `pywin32` with `; sys_platform == "win32"`. `pip freeze` drops that marker on Windows and the whole line on macOS, so restore it after regenerating the lock file.

## Upstream invariants

`tests/test_upstream_invariants.py` uses `tests/upstream_scan.py` to parse the installed package statically with AST. It never imports it, because importing would write to the real log file (`%APPDATA%\meta-ads-mcp` or `~/Library/Application Support/meta-ads-mcp`). The test fails when:
- upstream adds or removes a tool that isn't in `tool_policy.TOOL_CATEGORIES` (fix: classify it there),
- upstream uses an HTTP client other than `httpx` (that would bypass the write guard),
- names the launcher relies on disappear (`mcp_server = FastMCP(`, `entrypoint`, the `"meta-ads-mcp"` logger, the disable env flags).

When adding a launcher tool: add it to `LAUNCHER_TOOL_CATEGORIES` (and `RESULT_SIZE_LIMITS` if output can be large) and to the module's `TOOL_NAMES`.

## Conventions

- **Bilingual everything:** user-facing strings for the wizard and helper scripts live in `i18n.MESSAGES` for both `en` and `de`. Tests enforce identical keys and placeholders. Documentation changes must be made in both `README.md` and `README.de.md`.
- Never print or log token values. Scripts show at most the length and last 4 characters. Test fixtures use `FAKE_TOKEN`/`FAKE_PROOF` from `conftest.py`.
- `docs/screenshots/*.svg` are generated from a simulated wizard run (`tools/wizard_fakes.py`), with a leak check for paths, user names and token-like strings. Regenerate them after changing wizard output instead of editing the SVGs.
- New config is passed through env vars from the Claude Desktop entry (`META_*`) and parsed and validated in `settings.py` or `write_guard.policy_from_env`. Invalid values must stop startup and must not be ignored.
- `ROADMAP.md` is local and gitignored.
