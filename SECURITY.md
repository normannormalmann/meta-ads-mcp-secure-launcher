# Security Policy / Sicherheitsrichtlinie

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's private reporting instead: *Security* tab → *Report a vulnerability*.

Please include the affected version or commit, steps to reproduce and the impact. **Never include real tokens, app secrets or ad account data.** Use placeholders.

Vulnerabilities in `meta-ads-mcp` itself belong upstream: <https://github.com/pipeboard-co/meta-ads-mcp/security>.

## Sicherheitslücke melden

Bitte **kein** öffentliches Issue für Sicherheitsprobleme anlegen. Nutze stattdessen die vertrauliche Meldung auf GitHub: Reiter *Security* → *Report a vulnerability*.

Bitte gib die betroffene Version oder den Commit an, die Schritte zum Nachstellen und die Auswirkung. **Nie echte Tokens, App-Secrets oder Werbekontodaten mitschicken.** Verwende Platzhalter.

Lücken in `meta-ads-mcp` selbst gehören zum Originalprojekt: <https://github.com/pipeboard-co/meta-ads-mcp/security>.

## Scope / Umfang

In scope: `launch_secure.py`, `credential_store.py`, `secure_logging.py`, `write_guard.py`, `form_fields.py`, `url_policy.py`, `tool_policy.py`, `graph_client.py`, `extensions.py`, `instagram.py`, `store_secrets.py`, `check_token.py`, `setup_wizard.py`.
Known limitations are listed in the README ("Security model and limitations" / „Sicherheitsmodell und Grenzen“).
