# CLAUDE.md — cassandra-share

Ephemeral URL-gated share service for Claude Code conversation markdown.

## What it does

- Accepts sanitized markdown + metadata from authenticated users (`POST /share`)
- Returns a 128-bit-token URL (`https://share.cassandrasedge.com/s/<token>`)
- Serves the markdown publicly at that URL until expiry
- Supports `once=true` for self-destruct-after-first-fetch
- Owner can list their shares or revoke early

Sanitization happens client-side in `cass share create`. This service stores
whatever it's handed and trusts the client to redact. Never store raw
conversations — only sanitized markdown.

## Auth

- Admin routes (`POST /share`, `GET /share`, `DELETE /share/{token}`) are
  behind CF Access. Identity comes from `Cf-Access-Authenticated-User-Email`.
- `GET /s/{token}` is intentionally public. The URL token IS the capability.

## Storage

SQLite at `/data/share.db` (mounted PVC in k8s). Schema in `schema.sql`.
Expired rows purged on every `GET /share` call (lazy GC is fine at this scale).

## Env

- `SHARE_DB_PATH` (default `/data/share.db`)
- `SHARE_DOMAIN` (default `share.cassandrasedge.com`) — used for returned URLs
- `SHARE_MAX_BODY_BYTES` (default 5 MiB)
- `SHARE_DEFAULT_TTL_HOURS` (default 24)
- `SHARE_MAX_TTL_HOURS` (default 168 / one week)
- `HOST`, `PORT`

## Deploy

- Helm chart lives in `cassandra-k8s/apps/share/`
- Namespace: `production`
- Tunnel route: `share.cassandrasedge.com` → service `share:8080`
- CF Access policy: `share.cassandrasedge.com/share*` and `/share` require
  Google OAuth; `share.cassandrasedge.com/s/*` bypasses Access (public)
