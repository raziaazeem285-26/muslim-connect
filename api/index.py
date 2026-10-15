"""Vercel serverless entrypoint for Muslim Connect."""
import os
import sys
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402


class VercelPathFixMiddleware:
    """Vercel's rewrite invokes this function with the *rewritten* path
    (/api/index) instead of the original URL. vercel.json appends the real
    path as ?vercel_path=... — restore it so FastAPI routes correctly."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            try:
                qs = scope.get("query_string", b"").decode("utf-8", "ignore")
                params = parse_qsl(qs, keep_blank_values=True)
                real = None
                rest = []
                for k, v in params:
                    if k == "vercel_path" and real is None:
                        real = v
                    else:
                        rest.append((k, v))
                if real:
                    scope["path"] = real
                    scope["raw_path"] = real.encode("utf-8")
                    scope["query_string"] = urlencode(rest).encode("utf-8")
            except Exception:
                pass
        await self.asgi_app(scope, receive, send)


app.add_middleware(VercelPathFixMiddleware)

# Vercel's Python runtime imports this module and serves `app`.
