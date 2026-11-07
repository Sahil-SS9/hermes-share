# hermes-share

Share one or several Hermes sessions as a redacted, revocable link — and import a shared bundle
back into another Hermes, without a full profile export.

```
/share                        share this session
/share <id|title> [...]       share the named sessions (up to 12)
/share --last N               share the N most recent sessions
/share --format md|jsonl|both which files the gist carries (default: both)
/share confirm                upload the preview as a SECRET gist
/share cancel                 delete the preview and forget it
/share list                   gists this profile has shared
/share import <gist|file>     read a shared bundle and show what is inside
/share import confirm         import it into this Hermes
/unshare [id|last]            delete a shared gist and mark it revoked
```

Nothing is uploaded until you type the confirm command, and redaction has no off switch.

## Requirements

- **`ctx.redact_text()` in the host.** Redaction goes through the plugin API; there is deliberately
  no fallback that imports `agent.redact`. On a Hermes without it, `/share` refuses and names the
  missing seam instead of uploading anything. The core PR for that seam is
  `feat/plugin-redact-text`; it is not merged at the time of writing, so this plugin is not
  installable from the catalog yet (see `RELEASE.md`).
- **`gh`**, signed in (`gh auth login`). The plugin never reads or stores a token.
- **Hermes-to-Hermes import** additionally needs `hermes sessions import --from hermes`, which is
  open upstream PR [NousResearch/hermes-agent#87988](https://github.com/NousResearch/hermes-agent/pull/87988).
  It is feature-detected from `hermes sessions import --help`; without it the Hermes part of an
  import refuses by name and every other source still works.

## What a bundle is

Three files in one secret gist:

| File | Purpose |
| --- | --- |
| `hermes-sessions-<stamp>.jsonl` | the importable record, one session per line, exactly the shape `hermes sessions export` writes |
| `hermes-sessions-<stamp>.md` | the readable transcript |
| `hermes-session-<id>-<stamp>.html` | the standalone HTML page, one per session |

Every file comes from the public `hermes sessions export` CLI — the HTML included, which the CLI
renders itself (one call per session; it is the one format that refuses `-`). `--format` narrows the
set: `jsonl`, `md`, `html`, `both` (jsonl+md), `all` (the default), or a comma-separated list such as
`--format md,html`.

## What is redacted

Every string in every message goes through the host's strongest scrubber, then two more passes:

1. **Credentials** become non-reusable sentinels — `«redacted:sk-…»`, `«redacted:ghp_…»`,
   `«redacted-secret»`, `Bearer ***`. Not the head/tail mask a terminal can afford: ten readable
   characters of a key are fine on your own screen and not fine in a document that leaves the
   machine.
2. **Home paths** — `/home/<user>` and `/Users/<user>` become `~`.
3. **Identity** — your account name and host names become `<redacted>`.

The gist description is redacted with the same passes, and the preview reports how many of each.

## Preview

```
/share - preview ready, nothing uploaded

  sessions   2
  share-a-0001              4 msg    6 secret mark(s)  Deploy notes
  share-b-0002              2 msg    3 secret mark(s)  Second session

  redacted   9 secret mark(s), 2 home path(s), 2 account/host name(s)
  files      hermes-sessions-20261107-011530.jsonl, hermes-sessions-20261107-011530.md (4.1 KB total)
  gist desc  Hermes sessions: Deploy notes, Second session (2)

Next: /share confirm   uploads it as a SECRET gist (unlisted, not private)
      /share cancel    deletes the preview and forgets it
```

## Import

```
/share import https://gist.github.com/<you>/<id>
/share import ~/handover.jsonl
```

The format is decided from the file's content, never from its name or the URL:

| Content | Route |
| --- | --- |
| Hermes jsonl export (`id` + `messages` per line) | `hermes sessions import --from hermes` |
| Claude Code JSONL transcript | `hermes sessions import --from claude` |
| Codex rollout | `hermes sessions import --from codex` |

A gist carries the jsonl and the markdown; only the file that parses as session records is offered,
so the readable copy can never be imported as a transcript. Downloads are capped at 8 MB, use `gh`
for gists (a secret gist is not a public URL) and plain HTTPS otherwise, and nothing is executed.

## Surfaces

| Surface | Share / unshare | Import |
| --- | --- | --- |
| classic CLI (`hermes`) | full preview, then confirm | allowed |
| TUI (`hermes --tui`) | same output as the CLI | allowed |
| Desktop | same command dispatch | allowed |
| chat platforms (Discord, Telegram, Slack, …) | short preview in chat (counts and titles, never the bundle text), same confirm command, gist URL posted back | off by default: an import writes into this Hermes's store, so it needs `allow_gateway_import: true` |

```yaml
plugins:
  entries:
    hermes-share:
      settings:
        allow_gateway_import: true   # default false
```

## Disclosure

- **Reads:** the sessions you name, through the public `hermes sessions export` CLI. The session
  index (ids, titles, recency) is read through `hermes_state`, which is on the published plugin API
  list.
- **Writes:** the bundle and a 50-row ledger under `<hermes home>/plugin-data/hermes-share/`.
  Ledger rows hold the gist id, url, session ids/titles/counts, file names and timestamps. Previews
  and import downloads are capped at 20 files each; a pending preview expires after 15 minutes.
- **Network:** `gh gist create` and `gh gist delete` only after you confirm, never with `--public`;
  `gh gist view` to read a gist you import; plain HTTPS for a public raw URL.
- **Never:** uploads before confirm, turns redaction off, reads or stores a GitHub token, imports
  without `/share import confirm`, or executes anything it downloads.
- **Child processes:** `gh` gets an allowlist (`HOME`, `PATH`, `GH_CONFIG_DIR`, locale, `SHELL`,
  `TMPDIR`), and the `hermes` child gets the same kind of allowlist plus `HERMES_HOME` — never the
  session's whole environment, which holds every provider key on the machine.

## Desktop alternative

For a Desktop session as a long screenshot, HTML, Markdown or PDF with per-turn selection, use
[`hermes-chat-share`](https://github.com/smallmj/hermes-chat-share) from the catalog. It makes no
network calls. This plugin does not duplicate it.

## Tests

```
uv venv --python 3.14
uv pip install --python .venv/bin/python pytest "ruamel.yaml>=0.18"
HERMES_TEST_HOST_BIN=/path/to/hermes/.venv/bin/hermes \
PYTHONPATH=/path/to/hermes-agent-checkout .venv/bin/python -m pytest -q
```

69 tests against a temporary `HOME`/`HERMES_HOME` and a real `SessionDB`: bundle parsing, sentinel
masking, home and identity scrubbing, content detection for all three formats, the confirm gate (no
child process before it), the `#87988` refusal, the gateway consent gate, chat-platform sharing,
ledger bounds, TTLs, `/unshare`, and one end-to-end export through the real CLI binary.

## Licence

MIT.
