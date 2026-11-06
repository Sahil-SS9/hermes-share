"""Registration for hermes-share: two slash commands, no tools, no hooks."""

from __future__ import annotations

from . import PLUGIN_NAME, __version__
from .commands import handle_share, handle_unshare

_SHARE_HINT = "[md|html|jsonl] | confirm | cancel | list"
_UNSHARE_HINT = "[gist-id|last]"


def register(ctx) -> None:
    ctx.register_command(
        "share",
        handler=handle_share,
        description="Share this session as a redacted, revocable secret GitHub gist",
        args_hint=_SHARE_HINT,
    )
    ctx.register_command(
        "unshare",
        handler=handle_unshare,
        description="Delete a shared session gist (/unshare last for the newest)",
        args_hint=_UNSHARE_HINT,
    )


__all__ = ["register", "PLUGIN_NAME", "__version__"]
