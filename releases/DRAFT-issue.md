# Draft issue — "One-step redacted, revocable session link from the CLI and TUI"

Draft text for the plugin repository's own issue tracker, or for a feature issue on
`NousResearch/hermes-agent` if the maintainers prefer the gap tracked upstream. Not filed.

---

## What exists today

- `/save json|md|html [filename] [redact]` writes the current session to a local file.
- `agent/redact.py` masks credentials in everything Hermes prints.
- `hermes-chat-share` (catalog, Desktop only) exports a Desktop conversation to PNG, HTML,
  Markdown, JSON or PDF and makes no network calls.

## The gap

There is no one-step way to go from "this conversation" to "a link I can paste, that I can revoke"
from the terminal. Today that is: `/save md ... redact`, open the file, check it by eye, create a
gist by hand, remember its id, and delete it by hand later. Every step is a chance to upload the
un-redacted copy.

## Proposal

A small plugin, `hermes-share`, registering `/share` and `/unshare`:

- `/share [md|html|jsonl]` exports the session through `/save`'s own functions with redaction always
  on, writes a preview, and shows the path, size, redaction count and first twenty lines.
- `/share confirm` uploads that file as a secret gist through `gh`, records it in a bounded ledger,
  and prints the URL.
- `/unshare [id|last]` deletes it and marks it revoked.

Nothing is uploaded before `confirm`; redaction has no off switch; the plugin holds no token.

## Notes for the maintainers

The plugin uses `hermes_cli.session_export.load_save_snapshot` / `render_session_for_save` and
`agent.redact.redact_sensitive_text`, which `hermes plugins validate` reports as private core
imports. There is no public plugin seam that exports the current session, so the alternative would
be re-implementing `/save`'s rendering and drifting from it. If a `ctx`-level session-export seam is
wanted, this plugin is a concrete consumer for it.
