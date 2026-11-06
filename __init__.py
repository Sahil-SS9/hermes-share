"""Directory-plugin entry point for hermes-share.

Hermes mounts this file as ``hermes_plugins.<slug>`` with the repository root on the module's
search path, so a relative import of the sibling package works for a directory install. A wheel or
entry-point install puts the package on ``sys.path`` directly, which the absolute fallback covers.
Keep the import inside ``register()``: a module-level relative import makes pytest import this file
standalone during collection, and the host's capability probe runs ``register()`` in a recording
context where nothing may be imported eagerly.
"""

from __future__ import annotations


def register(ctx) -> None:
    try:
        from .hermes_share.plugin import register as _register  # directory install
    except ImportError:  # installed package / repository root on sys.path
        from hermes_share.plugin import register as _register  # type: ignore[no-redef]
    _register(ctx)
