"""FastAPI app for cassandra-share.

Routes:
  POST   /share          (auth)   create a new share, returns {token, url, expires_at}
  GET    /share                   list current user's shares (auth)
  DELETE /share/{token}  (auth)   revoke a share (owner only)
  GET    /s/{token}               public — capability-URL fetch; 404 if missing/expired;
                                  deletes row if once=1 after serving
  GET    /healthz                 liveness

Auth model:
  Admin routes (POST/DELETE/GET /share) read the user's email from CF Access
  headers passed through by cloudflared: "Cf-Access-Authenticated-User-Email".
  In dev we also honor X-Dev-Email for local testing.

  /s/{token} is intentionally unauthenticated — the 128-bit token in the URL
  IS the capability.
"""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field


DB_PATH = Path(os.environ.get("SHARE_DB_PATH", "/data/share.db"))
# DOMAIN: hostname embedded in returned URLs. The public GET path lives on
# SHARE_PUBLIC_DOMAIN (no CF Access); admin routes live on DOMAIN (CF Access).
DOMAIN = os.environ.get("SHARE_DOMAIN", "share.cassandrasedge.com")
PUBLIC_DOMAIN = os.environ.get("SHARE_PUBLIC_DOMAIN", "s.cassandrasedge.com")
MAX_BODY_BYTES = int(os.environ.get("SHARE_MAX_BODY_BYTES", 5 * 1024 * 1024))  # 5 MB
DEFAULT_TTL_HOURS = int(os.environ.get("SHARE_DEFAULT_TTL_HOURS", 24))
MAX_TTL_HOURS = int(os.environ.get("SHARE_MAX_TTL_HOURS", 7 * 24))  # one week


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token() -> str:
    # 22-char base64url, 128 bits of entropy.
    import base64
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode()


async def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema = (Path(__file__).resolve().parent.parent.parent / "schema.sql").read_text()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(schema)
        await db.commit()


async def _purge_expired() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM shares WHERE expires_at < datetime('now') "
            "OR (once = 1 AND consumed_at IS NOT NULL)"
        )
        await db.commit()
        return cur.rowcount


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _init_db()
    yield


app = FastAPI(title="cassandra-share", lifespan=lifespan)


# ── Auth ────────────────────────────────────────────────────────────────────


def _require_email(request: Request) -> str:
    """Pull the authenticated email from CF Access headers; fail closed."""
    email = (
        request.headers.get("cf-access-authenticated-user-email")
        or request.headers.get("x-dev-email")  # dev only
    )
    if not email:
        raise HTTPException(status_code=401, detail="Missing CF Access identity")
    return email.lower()


# ── Models ──────────────────────────────────────────────────────────────────


class CreateShare(BaseModel):
    body: str = Field(..., description="Sanitized markdown payload.")
    title: str | None = None
    summary: str | None = None
    ttl_hours: int = Field(default=DEFAULT_TTL_HOURS, ge=1, le=MAX_TTL_HOURS)
    once: bool = False


class CreateShareResponse(BaseModel):
    token: str
    url: str
    expires_at: str
    once: bool


class ShareMeta(BaseModel):
    token: str
    title: str | None
    summary: str | None
    once: bool
    created_at: str
    expires_at: str
    url: str


# ── Routes ──────────────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"ok": "yes"}


@app.post("/share", response_model=CreateShareResponse)
async def create_share(payload: CreateShare, request: Request) -> CreateShareResponse:
    email = _require_email(request)
    if len(payload.body.encode()) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"Body exceeds {MAX_BODY_BYTES} bytes")

    token = _token()
    expires = _now() + timedelta(hours=payload.ttl_hours)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO shares (token, owner_email, title, summary, body, once, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token, email, payload.title, payload.summary, payload.body,
             1 if payload.once else 0, expires.isoformat()),
        )
        await db.commit()

    return CreateShareResponse(
        token=token,
        url=f"https://{PUBLIC_DOMAIN}/s/{token}",
        expires_at=expires.isoformat(),
        once=payload.once,
    )


@app.get("/share", response_model=list[ShareMeta])
async def list_shares(request: Request) -> list[ShareMeta]:
    email = _require_email(request)
    await _purge_expired()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT token, title, summary, once, created_at, expires_at
               FROM shares WHERE owner_email = ? ORDER BY created_at DESC""",
            (email,),
        )
        rows = await cur.fetchall()
    return [
        ShareMeta(
            token=r["token"],
            title=r["title"],
            summary=r["summary"],
            once=bool(r["once"]),
            created_at=r["created_at"],
            expires_at=r["expires_at"],
            url=f"https://{PUBLIC_DOMAIN}/s/{r['token']}",
        )
        for r in rows
    ]


@app.delete("/share/{token}")
async def revoke_share(token: str, request: Request) -> dict[str, str]:
    email = _require_email(request)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM shares WHERE token = ? AND owner_email = ?",
            (token, email),
        )
        await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Share not found or not owned by you")
    return {"revoked": token}


@app.get("/s/{token}", response_class=PlainTextResponse)
async def fetch_share(token: str) -> PlainTextResponse:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT body, once, consumed_at FROM shares
               WHERE token = ?
                 AND expires_at > datetime('now')
                 AND (once = 0 OR consumed_at IS NULL)""",
            (token,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found or expired")
        if row["once"]:
            await db.execute(
                "UPDATE shares SET consumed_at = datetime('now') WHERE token = ?",
                (token,),
            )
            await db.commit()
    return PlainTextResponse(row["body"], media_type="text/markdown; charset=utf-8")
