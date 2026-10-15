# Muslim Connect 🕌

**One Ummah. One Community. Respect for All.**

A production-ready, worldwide Muslim community chat platform: real-time chat rooms,
private/group messaging, an Islamic Q&A knowledge center, verified-scholar workflow,
moderation tooling, and full admin dashboard — in English and Urdu (RTL), with an
emerald/white/gold Islamic-inspired design.

## What was reused from this workspace

Per the build brief, existing resources were audited first:

| Resource | Verdict |
|---|---|
| `raziaazeem285-26/ustadh-hub` (GitHub) | Static Islamic blog site — no chat/backend code to reuse; design language noted |
| `-awaz-studio` (GitHub) | FastAPI + static-frontend pattern reused for this app's architecture |
| Supabase / external realtime DB | Not connected — no Supabase connector exists in this workspace |
| Eden AI connector | Image generation returns 402 (no credits); not usable under zero-budget constraint |
| Existing chat/auth/backend infra | None existed — built new |

**Decision:** single deployable monolith — FastAPI backend + SQLite (Postgres-ready) +
WebSocket real-time + dependency-free SPA frontend. No paid services, no build step.

## Features

- **Auth:** register/login, email verification, password reset, brute-force lockout,
  secure sessions (JWT), account deletion, data export
- **Guest mode:** instant access to public rooms, temporary identity, 72h expiry,
  message caps, one-tap upgrade to a full account (profile preserved)
- **Real-time chat:** public/country/language/topic rooms, typing indicators,
  presence, reactions, replies, edit (15 min), delete, mentions → notifications,
  image uploads (validated), unread counts, pagination, auto-reconnect,
  idempotent sends (no duplicates)
- **DMs & groups:** 1:1 conversations, group chats with admins, block-aware
- **Islamic Q&A:** categories (Quran, Hadith, Seerah, Fiqh, General), answers typed as
  quotation / translation / interpretation / opinion, citations with references and
  verified/unverified flags, useful-votes, accepted answers, bookmarks
- **Scholars:** application form + document upload → human admin review →
  Verified Scholar badge, searchable directory, disclaimer
- **Moderation:** reports (message/user/room/question/answer), warn / delete /
  room-mute / suspend / ban / unban, appeals (works even while banned),
  block/mute lists, audit log of every privileged action
- **Admin:** overview stats, user management + roles, room controls (slow mode,
  disable), site settings (announcement, community rules), audit trail
- **i18n:** English + Urdu with RTL layout, language switcher preserves page;
  architecture ready for Arabic
- **Theming:** light/dark mode; PWA manifest; responsive mobile → desktop

## Quick start

```bash
cd muslim-connect
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # then edit: set SECRET_KEY (required in production)
.venv/bin/python run.py     # → http://localhost:8000
```

First admin: set `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` in `.env`
before the first run (creates the admin only if none exists), then remove them.

Demo accounts (seeded, clearly fictional): `demo_user`, `demo_moderator`,
`demo_scholar` — password `demo-password-123`.

## Project structure

```
muslim-connect/
  app/
    main.py            # app factory, lifespan, static/SPA serving
    config.py          # env-based settings (.env)
    database.py        # engine + session
    models.py          # 20-table schema (users, rooms, messages, QA, scholars,
                       #   reports, bans, notifications, audit…)
    schemas.py         # pydantic I/O contracts
    security.py        # bcrypt, JWT, rate limiter, spam/content helpers
    deps.py            # auth deps, RBAC, audit/notify helpers
    mail.py            # SMTP with console fallback
    seed.py            # 10 default rooms, settings, demo accounts
    routers/           # auth, chat (+WebSocket), qa, scholars, moderation,
                       #   admin, users, notifications
    static/            # SPA: index.html, app.js, styles.css, i18n/, manifest.json
    uploads/           # validated image uploads (gitignored in real use)
  tests/test_app.py    # 24 integration tests
  requirements.txt  .env.example  run.py  README.md  DEPLOYMENT.md
```

## API overview

| Area | Key endpoints |
|---|---|
| Auth | `POST /api/auth/register|login|guest|logout`, `/verify`, `/password/reset-*`, `/me` (DELETE) |
| Rooms | `GET /api/rooms`, `POST /api/rooms`, `GET/POST /api/rooms/{slug}/messages`, `POST …/upload` |
| Chat | `PATCH/DELETE /api/messages/{id}`, `POST …/reactions`, `WS /api/ws?token=` |
| DMs | `GET/POST /api/conversations`, `/dm`, `/group`, `/{id}/messages` |
| Q&A | `GET/POST /api/qa/questions`, `/questions/{id}/answers`, `/answers/{id}/useful` |
| Scholars | `POST /api/scholars/apply`, `/apply/{id}/documents`, `GET /directory`, admin `/applications` |
| Moderation | `POST /api/moderation/reports|appeals|actions|block/{id}`, `GET /reports` |
| Admin | `GET /api/admin/overview|users|rooms|settings|audit`, `POST …/settings/{key}` |
| Users | `GET /api/users/directory`, `/{id}`, `PATCH /users/me`, bookmarks, `/me/export` |
| Notify | `GET /api/notifications`, `/unread-count`, `POST /{id}/read`, `/read-all` |

Interactive docs (dev only): `http://localhost:8000/api/docs`

## Security notes

- Passwords: bcrypt. Sessions: signed JWT (never in URLs except the WS query param).
- Server-side authorization on every protected route; roles checked server-side.
- Rate limits: auth 10/min, messages 20/min, API 120/min (env-tunable).
- Uploads: MIME allowlist + magic-byte validation + size cap; served as static files.
- Secrets only via env; `.env.example` documents everything; no keys in code.
- Messages are **not** end-to-end encrypted — stated honestly in the Privacy page.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

24 integration tests covering: registration/login, duplicate rejection, brute-force
lockout, guest entry, email verification & reset flows, room listing, guest posting,
two-user message exchange with reply + mention notification, edit/delete,
edit-authorization, reactions, members-room guest denial, spam blocking, **live
WebSocket broadcast between two sessions**, DM flow + block, private-conversation
authorization, Q&A lifecycle + guest denial, scholar application gating,
report → moderator action → message removal, user moderation denial, admin denial,
ban → post denial → appeal, audit-log admin denial.

**Result: 24 passed** (run 2026-10-15).

## Limitations / honest gaps

- Email sending needs SMTP credentials; without them, links print to the server console.
- Real-time hub is in-process: for multi-worker production, swap `Hub` for Redis pub/sub.
- No Google sign-in (no OAuth credentials available); no web-push (no VAPID keys).
- 2FA not implemented (documented as future work; password policy + lockout in place).
- Not load-tested — no claims about concurrent-user capacity.
- Not yet deployed publicly (needs your hosting choice + `SECRET_KEY` + Postgres).
