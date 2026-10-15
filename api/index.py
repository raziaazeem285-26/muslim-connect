"""Vercel serverless entrypoint for Muslim Connect (with import-error surfacing)."""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.main import app  # noqa: E402
except Exception:
    from fastapi import FastAPI  # noqa: E402

    app = FastAPI()
    _err = traceback.format_exc()

    @app.get("/{full_path:path}")
    async def import_error(full_path: str):
        return {"status": "import_failed", "path": full_path, "traceback": _err}
