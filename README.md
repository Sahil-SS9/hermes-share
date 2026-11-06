# hermes-share

Share the conversation you are in as a redacted, revocable link, without leaving the terminal.

`/share` writes a preview of the current session to your plugin-data directory, shows you the file
path, its size, how many secrets it removed and the first twenty lines. Nothing leaves the machine
until you type `/share confirm`, and `/unshare` deletes the gist again.

```
/share [md|html|jsonl]   write a redacted preview (md is the default)
/share confirm           upload the preview as a SECRET gist
/share cancel            delete the preview and forget it
/share list              gists this profile has shared
/unshare [id|last]       delete a shared gist and mark it revoked
```

## Why

Hermes already has `/save json|md|html [filename] [redact]`, which writes a local file, and
`agent/redact.py`, which is the engine behind every credential mask Hermes prints. What it does not
have is a one-step way to go from "this conversation" to "a link I can paste, that I can revoke".
`hermes-chat-share` covers the Desktop side by exporting to PNG, HTML, Markdown, JSON or PDF; this
plugin is the CLI and TUI counterpart and makes exactly one kind of network call, through `gh`.

## Install

```
hermes plugins install Sahil-SS9/hermes-share
```

For development, copy the repository into `<hermes home>/plugins/hermes-share/` and add
`hermes-share` to `plugins.enabled` in `config.yaml`; the plugin loads as a directory plugin.

Requires the [GitHub CLI](https://cli.github.com/) and `gh auth login`. The plugin never reads,
stores or takes a token: authentication is whatever `gh` already has for your account.

## What a preview looks like

This is real output from a throwaway Hermes home with a session containing planted credentials:

```
/share - preview ready, nothing uploaded

  file       <hermes home>/plugin-data/hermes-share/previews/hermes_session_6bd32f79614dfd51.md
  format     md
  size       998 B, 44 lines
  redacted   12 secret mark(s), 3 home path(s), 3 account/host name(s)
  gist desc  Hermes session: untitled session 6bd32f79614dfd51 (06/11/2026)

  --- first 20 lines ---
# Session: share-live-01
- Session ID: `share-live-01`
- Source: `cli`
- Model: `live-test-model`

## User - 2026-11-06T13:06:56Z

here are my deploy notes
OPENAI key «redacted:sk-…»
CI token «redacted:ghp_…»
header Authorization: Bearer ***
DB_PASSWORD=«redacted-secret»
checked out at ~/work/private-repo/app.py and I am <redacted>
  ... (24 more lines)
  --- end ---

Next: /share confirm   uploads it as a SECRET gist (unlisted, not private)
      /share cancel    deletes the preview and forgets it
```

After `/share confirm`:

```
Shared as a secret gist:
  https://gist.github.com/<you>/<id>

  998 B, format md, hermes_session_6bd32f79614dfd51.md
  Secret means unlisted and searchable only by URL - it is not access-controlled.

Revoke with: /unshare last
```

`/unshare last`:

```
Deleted gist <id>. The URL now returns 404.
  Revoked at 2026-11-06T13:12:33Z
```

## What it redacts

Three passes, each counted in the preview, with no way to switch any of them off:

1. Every message body, tool-call argument and segment goes through
   `agent.redact.redact_sensitive_text` in its strongest mode. Credentials become non-reusable
   sentinels such as `«redacted:sk-…»` rather than the `sk-pro...z2Xc` head/tail mask a terminal
   prints: ten readable characters of a key are fine on your own screen and not fine in a document
   that leaves the machine.
2. The whole rendered document is passed through the force-mode pass again, which also covers the
   metadata the message pass does not touch (title, model, timings).
3. `/home/<user>` and `/Users/<user>` are rewritten to `~`, and your account name and host names
   are replaced with `<redacted>`.

Redaction is applied to the gist description as well as the body.

## Disclosure

- **Reads:** the current session, through the same export functions `/save` uses
  (`hermes_cli.session_export.load_save_snapshot` / `render_session_for_save`), read-only.
- **Writes:** the redacted export and a bounded ledger under
  `<hermes home>/plugin-data/hermes-share/`. The ledger keeps the last 50 shares (gist id, url,
  session hash, format, timestamps, status) and never stores the session id itself. Previews are
  capped at 20 files and pending previews expire after 15 minutes.
- **Network:** exactly two calls, both through the `gh` CLI and both only after you confirm:
  `gh gist create` and `gh gist delete`. `--public` is never passed and a test asserts it.
- **Never:** uploads without `/share confirm`, turns redaction off, reads or stores a GitHub token,
  or runs any other subprocess. The preview itself starts no child process at all — the `gh auth
  status` check happens on `confirm`, not on `share`.
- **Child environment:** `gh` is given an allowlist (`HOME`, `PATH`, `GH_CONFIG_DIR`, `GH_HOST`,
  `XDG_CONFIG_HOME`, locale, `SHELL`, `TMPDIR`), never the session's whole environment, which holds
  every provider key on the machine.

## Surfaces

| Surface | Behaviour |
| --- | --- |
| classic CLI (`hermes`) | supported, preview + confirm |
| TUI (`hermes --tui`) | supported, same output |
| Desktop | supported through the same command dispatch |
| messaging platforms | refused with a message pointing at `/save` |

Messaging platforms are refused deliberately rather than half-implemented: the preview has to be
read somewhere you can also open the file, and a chat reply cannot carry twenty lines plus a
confirm round trip without putting the document into the chat history. Gateway support is a
follow-up.

## Desktop

If you want to share a Desktop conversation as a long screenshot, self-contained HTML, Markdown,
JSON or PDF, use [`hermes-chat-share`](https://github.com/smallmj/hermes-chat-share) from the Hermes
catalog. It is a Desktop UI plugin and makes no network calls. This plugin does not duplicate it.

## Compatibility

The plugin targets upstream `hermes-agent` at `90d6b98fd86d20981ee06ddd5b7d93e4320f26a4` and uses
three host modules that are not part of the documented plugin API:

```
agent.redact.redact_sensitive_text
hermes_cli.session_export.load_save_snapshot
hermes_cli.session_export.render_session_for_save
```

`hermes plugins validate` reports these as private core imports (a catalog rule 9 warning, not a
failure). There is no public plugin seam for exporting the current session, so reusing `/save`'s
own functions is the smallest honest option: it means a shared session is byte-for-byte the
document `/save` would have written, redacted. If any of those signatures change, the plugin needs
updating with the host.

## Tests

```
uv venv --python 3.14
uv pip install --python .venv/bin/python pytest "ruamel.yaml>=0.18"
HERMES_HOME=/tmp/unused PYTHONPATH=/path/to/hermes-agent-checkout \
  .venv/bin/python -m pytest -q
```

Fifty-five tests. They run against a temporary `HOME` and `HERMES_HOME` with a real `SessionDB`,
and cover: planted credentials, bearer headers and `.env` lines never reaching the document or the
gist description; the confirm gate (no child process before `confirm`); a bounded ledger; `/unshare
last`; `gh` missing and `gh` not signed in; the `--public` and `--yes` argument shapes; preview TTL;
and the surface allowlist.

## Licence

MIT.
