# Release notes — hermes-share

## Do not submit to the catalog yet

`plugin.yaml` requires `ctx.redact_text()`, which is not on upstream `main`. The catalog entry is
drafted and deliberately **not** submitted: `RELEASE.md` and the entry's `known_issues` say so, and
`plugin_validate_private_imports` has nothing to report here — the plugin has no private imports, it
has a missing *public* one.

Drafted entry: `plugin-catalog/hermes-share.yaml`, pinned to
`7da00a2f8cc38f685260a6eff61cb68532c99b3c` — a LOCAL commit that is not pushed anywhere, so the pin
is not fetchable yet. Structural check against the host's own validator:
`scripts/validate_plugin_catalog.py plugin-catalog/hermes-share.yaml` → `OK: 1 file(s) valid`
(category must be one of desktop/memory/platform/web/tools/voice/automation/models/general;
`tools` is the fit).

Order of work before a submission:

1. `feat/plugin-redact-text` merges upstream. Record the released version that carries it.
2. Re-run `hermes plugins validate` and `hermes plugins doctor --ci` against a clean checkout of the
   merged host, not a personal branch.
3. Tag `v0.2.0`; pin `plugin-catalog/hermes-share.yaml` to that release commit (full 40-hex).
4. Only then open the catalog PR from the repository owner.

## What 0.2.0 changes against 0.1.0

- **Bundles.** `/share` still shares the current session; it now also takes ids or title fragments
  and `--last N`, and produces two files in one gist (importable jsonl + readable md).
- **Import.** `/share import <gist|file>` previews and then imports a shared bundle, a Claude Code
  transcript or a Codex rollout, with the format detected from content.
- **No private core imports.** The export/import path is the public CLI, and redaction is
  `ctx.redact_text`. `hermes plugins validate` reports no private core imports.
- **Chat platforms** get real support for sharing (short preview, same confirm, URL back). Import
  from chat is gated behind `allow_gateway_import` because it writes into this Hermes's store.
- **html** is back and is part of the default bundle: `hermes sessions export --format html`
  renders it, one call per session, so the plugin keeps no private renderer. 0.1.0's html was
  produced by `hermes_cli.session_export`, which is exactly what this lane removed.
- Branch renamed `master` → `main`.

## Known limitations

- A Hermes-to-Hermes import needs upstream PR #87988. Without it the Hermes part of an import
  refuses by name; Claude Code and Codex imports work today.
- The bundle cap is 12 sessions and 400,000 characters of messages per session.
- Chat-platform previews are summaries (counts and titles) by design; the bundle text is never
  posted into a chat.

## Release checklist

- [ ] `ctx.redact_text` released upstream.
- [ ] `pytest -q` green (62 tests) with `HERMES_TEST_HOST_BIN` set.
- [ ] `hermes plugins validate <repo>` — "Validation passed", zero private core imports.
- [ ] `hermes plugins doctor <repo> --ci` — OK.
- [ ] Live run: two fake homes A→B, gist created and deleted, imports resumed in both interfaces.
- [ ] `plugin.yaml`, `pyproject.toml`, `LICENSE` and the catalog entry all name `Sahil-SS9`.
- [ ] Catalog `sha:` is a full 40-hex release commit that is fetchable (`git ls-remote origin`).
- [ ] No exact `==` pins in `pyproject.toml`; `uv.lock` committed.
