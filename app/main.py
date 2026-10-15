"""Muslim Connect — FastAPI application entrypoint."""
from __future__ import annotations

import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import models
from .config import settings
from .database import Base, SessionLocal, engine
from .routers import admin, auth, chat, moderation, notifications, qa, scholars, users
from .seed import seed

from .paths import writable_dir

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = writable_dir(os.path.join(BASE_DIR, "uploads"), "mc_uploads")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed(db)
        # bootstrap admin from env (first run only)
        if settings.BOOTSTRAP_ADMIN_EMAIL and settings.BOOTSTRAP_ADMIN_PASSWORD:
            exists = db.query(models.User).filter(models.User.role == models.Role.ADMIN).first()
            if not exists:
                from .security import hash_password
                admin = models.User(
                    username=settings.BOOTSTRAP_ADMIN_EMAIL.split("@")[0] + "_admin",
                    display_name="Administrator", email=settings.BOOTSTRAP_ADMIN_EMAIL.lower(),
                    password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
                    role=models.Role.ADMIN, email_verified=True,
                )
                db.add(admin)
                db.commit()
    finally:
        db.close()
    yield


app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs" if settings.ENV != "production" else None)

# --- middleware -------------------------------------------------------------
if settings.ENV == "production":
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])  # tighten in real deploy

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL] if settings.ENV == "production" else ["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


# --- startup ----------------------------------------------------------------
# (moved into lifespan handler above)


# --- API routers ------------------------------------------------------------
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(qa.router)
app.include_router(scholars.router)
app.include_router(moderation.router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(notifications.router)


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.APP_NAME, "env": settings.ENV}


@app.get("/api/config")
def public_config():
    """Branding + feature flags safe for the public frontend."""
    return {"app_name": settings.APP_NAME, "tagline": settings.TAGLINE,
            "max_upload_mb": settings.MAX_UPLOAD_MB}


# --- uploaded images (served with safe content-type) ------------------------


@app.get("/uploads/{fname}")
def serve_upload(fname: str):
    safe = os.path.basename(fname)
    path = os.path.join(UPLOAD_DIR, safe)
    if not os.path.isfile(path):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(path)


# --- SEO: robots.txt, sitemap.xml, crawlable room pages ----------------------
from fastapi.responses import PlainTextResponse  # noqa: E402
import html as _html  # noqa: E402
import datetime as _dt  # noqa: E402


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    base = str(request.base_url).rstrip("/")
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {base}/sitemap.xml\n"
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    base = str(request.base_url).rstrip("/")
    today = _dt.date.today().isoformat()
    urls = [(f"{base}/", today, "daily")]
    try:
        db = SessionLocal()
        try:
            rooms = (
                db.query(models.Room)
                .filter(models.Room.is_active == True, models.Room.visibility == "public")  # noqa: E712
                .order_by(models.Room.name)
                .all()
            )
            for r in rooms:
                urls.append((f"{base}/rooms/{r.slug}", today, "hourly"))
        finally:
            db.close()
    except Exception:
        pass  # never 500 for crawlers; serve at least the homepage
    items = "\n".join(
        f'  <url><loc>{_html.escape(u)}</loc><lastmod>{lm}</lastmod>'
        f"<changefreq>{cf}</changefreq></url>"
        for u, lm, cf in urls
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}\n</urlset>'
    return PlainTextResponse(xml, media_type="application/xml")


@app.get("/rooms/{slug}", include_in_schema=False)
def room_page(slug: str, request: Request):
    """Server-rendered public room page so search engines can crawl real content."""
    from .security import sanitize_text

    try:
        db = SessionLocal()
        try:
            room = (
                db.query(models.Room)
                .filter(models.Room.slug == slug, models.Room.is_active == True)  # noqa: E712
                .first()
            )
            if not room or room.visibility != "public":
                return JSONResponse({"detail": "Not found"}, status_code=404)
            msgs = (
                db.query(models.Message)
                .filter(models.Message.room_id == room.id, models.Message.is_deleted == False)  # noqa: E712
                .order_by(models.Message.id.desc())
                .limit(20)
                .all()
            )
        finally:
            db.close()
    except Exception:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    base = str(request.base_url).rstrip("/")
    title = f"{sanitize_text(room.name)} — Muslim Connect Chat Room"
    desc = sanitize_text(room.description or f"Join the {room.name} Muslim chat room on Muslim Connect.")
    msg_html = "".join(
        f"<li><strong>{_html.escape(m.author.display_name or m.author.username)}</strong>: "
        f"{_html.escape(sanitize_text(m.body or ''))}</li>"
        for m in reversed(msgs)
    ) or "<li>No messages yet — be the first to say salam!</li>"
    import json as _json  # noqa: E402
    from fastapi.responses import HTMLResponse  # noqa: E402

    ld = _json.dumps({
        "@context": "https://schema.org",
        "@type": "DiscussionForumPosting",
        "headline": sanitize_text(room.name),
        "url": f"{base}/rooms/{room.slug}",
    })
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html.escape(title)}</title>
<meta name="description" content="{_html.escape(desc)}">
<link rel="canonical" href="{base}/rooms/{_html.escape(room.slug)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{_html.escape(title)}">
<meta property="og:description" content="{_html.escape(desc)}">
<meta property="og:url" content="{base}/rooms/{_html.escape(room.slug)}">
<script type="application/ld+json">{ld}</script>
</head>
<body style="font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#1a1a1a">
<h1>🌙 {_html.escape(sanitize_text(room.name))}</h1>
<p>{_html.escape(desc)}</p>
<p><a href="{base}/#/" style="display:inline-block;background:#065f46;color:#fff;padding:.6rem 1.2rem;border-radius:.5rem;text-decoration:none">Join the conversation — free</a></p>
<h2>Recent messages</h2>
<ul>{msg_html}</ul>
<footer><p><a href="{base}/">Muslim Connect</a> — One Ummah. One Community. Respect for All.</p></footer>
</body></html>"""
    return HTMLResponse(page)


# --- frontend SPA -----------------------------------------------------------
@app.get("/", include_in_schema=False)
def spa_root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str):
    # hash-routed SPA: every non-API route serves index.html
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    candidate = os.path.join(STATIC_DIR, path)
    if os.path.isfile(candidate):
        return FileResponse(candidate)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
