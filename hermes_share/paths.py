"""Plugin-data resolution with a named refusal instead of a home-directory fallback.

``plugin_data_dir`` re-resolves the active Hermes home on every call, which is what a
multi-profile host needs: caching the path in a module global would keep writing to the profile
that happened to be active the first time. When the host cannot resolve it we refuse by name --
never fall back to ``~/.hermes``, because that fallback materialises a directory under whatever
profile the process happens to be pointing at.
"""

from __future__ import annotations

from pathlib import Path

from . import PLUGIN_NAME


class StateRootUnavailable(RuntimeError):
    """The host could not resolve the plugin-data root; nothing was written."""


def data_dir() -> Path:
    """``<active hermes home>/plugin-data/hermes-share/``, created on demand."""
    try:
        from plugins.plugin_storage import plugin_data_dir
    except Exception as exc:  # pragma: no cover - broken host install
        raise StateRootUnavailable(
            "cannot resolve the Hermes plugin-data directory: the host's plugin storage module "
            "is unavailable"
        ) from exc
    try:
        return plugin_data_dir(PLUGIN_NAME)
    except Exception as exc:  # pragma: no cover - refused or unreadable root
        raise StateRootUnavailable(
            "cannot resolve the Hermes plugin-data directory: the host refused the path"
        ) from exc


def previews_dir() -> Path:
    path = data_dir() / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pending_dir() -> Path:
    path = data_dir() / "pending"
    path.mkdir(parents=True, exist_ok=True)
    return path
