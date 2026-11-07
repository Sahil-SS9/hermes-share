"""Registration for hermes-share: two slash commands, no tools, no hooks.

The host object is bound into the handlers here, once. A CLI handler receives a Namespace rather
than the plugin context, and a slash command receives only ``raw_args``, so nothing downstream can
assume it has ``ctx`` -- ``Runtime`` is the one place that holds it.
"""

from __future__ import annotations

from functools import partial

from . import PLUGIN_NAME, __version__
from .commands import GATEWAY_IMPORT_SETTING, Runtime, handle_share, handle_unshare

_SHARE_HINT = "[<id|title> ...] | --last N | confirm | cancel | list | import <gist|file>"
_UNSHARE_HINT = "[gist-id|last]"


def register(ctx) -> None:
    runtime = Runtime(ctx)
    ctx.register_command(
        "share",
        handler=partial(handle_share, rt=runtime),
        description="Share sessions as a redacted, revocable secret GitHub gist, and import them back",
        args_hint=_SHARE_HINT,
    )
    ctx.register_command(
        "unshare",
        handler=partial(handle_unshare, rt=runtime),
        description="Delete a shared session gist (/unshare last for the newest)",
        args_hint=_UNSHARE_HINT,
    )


__all__ = ["register", "PLUGIN_NAME", "__version__", "GATEWAY_IMPORT_SETTING"]
