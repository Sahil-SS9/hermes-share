"""Registration: two slash commands, no tools, no hooks. The host object is bound into the
handlers here, once, through :class:`Runtime`."""

from __future__ import annotations

from functools import partial

from .commands import Runtime, handle_share, handle_unshare


def register(ctx) -> None:
    runtime = Runtime(ctx)
    ctx.register_command(
        "share",
        handler=partial(handle_share, rt=runtime),
        description="Share sessions as a redacted, revocable secret GitHub gist, and import them back",
        args_hint="[<id|title> ...] | --last N | confirm | cancel | list | import <gist|file>",
    )
    ctx.register_command(
        "unshare",
        handler=partial(handle_unshare, rt=runtime),
        description="Delete a shared session gist (/unshare last for the newest)",
        args_hint="[gist-id|last]",
    )
