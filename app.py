"""Vercel entrypoint — root FastAPI app (same proven pattern as AwazStudio).

Vercel auto-detects this file and routes every request through the `app`
object below, so no vercel.json rewrites are needed.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Defensive: if the runtime loaded THIS file itself as module "app",
# drop that alias so the real `app/` package can be imported below.
_mod = sys.modules.get("app")
if _mod is not None and getattr(_mod, "__file__", "") == os.path.abspath(__file__):
    del sys.modules["app"]

from app.main import app  # noqa: E402  (Vercel serves this `app`)
