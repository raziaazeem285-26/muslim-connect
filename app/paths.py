"""Filesystem helpers for read-only hosts (e.g. Vercel serverless)."""
from __future__ import annotations

import os
import tempfile


def writable_dir(preferred: str, fallback_name: str) -> str:
    """Return `preferred` if we can create+write there, else a tmpdir fallback."""
    try:
        os.makedirs(preferred, exist_ok=True)
        probe = os.path.join(preferred, ".writetest")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return preferred
    except Exception:
        fb = os.path.join(tempfile.gettempdir(), fallback_name)
        os.makedirs(fb, exist_ok=True)
        return fb


def writable_sqlite_url(url: str) -> str:
    """If a sqlite URL points at a non-writable location, redirect to tmpdir."""
    if not url.startswith("sqlite"):
        return url
    path = url.split("sqlite:///", 1)[-1]
    if not path or path == ":memory:":
        return url
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        probe = os.path.join(directory, ".writetest")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return url
    except Exception:
        name = os.path.basename(path) or "app.db"
        return f"sqlite:///{os.path.join(tempfile.gettempdir(), name)}"
