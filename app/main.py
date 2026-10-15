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


# --- frontend SPA -----------------------------------------------------------
@app.get("/", include_in_schema=False)
def spa_root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str, request: Request):
    # hash-routed SPA: every non-API route serves index.html
    if path.startswith("api/"):
        hdrs = {}
        for k, v in request.scope.get("headers", []):
            try:
                hdrs[k.decode()] = v.decode()
            except Exception:
                pass
        qs = request.scope.get("query_string", b"")
        srv = request.scope.get("server")
        return JSONResponse({"detail": "Not found", "got_path": path,
                             "asgi_path": request.scope.get("path"),
                             "root_path": request.scope.get("root_path"),
                             "query_string": qs.decode() if isinstance(qs, bytes) else qs,
                             "server": list(srv) if srv else None,
                             "headers": hdrs}, status_code=404)
    candidate = os.path.join(STATIC_DIR, path)
    if os.path.isfile(candidate):
        return FileResponse(candidate)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
