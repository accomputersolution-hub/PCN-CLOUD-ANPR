# Firebase (pcn-anpr) — primary backend

Firebase is the **authoritative** backend for PCN Cloud ANPR.

| Concern | Value |
| --- | --- |
| Auth | `AUTH_PROVIDER=firebase` |
| Datastore | `DATASTORE_PROVIDER=firestore` |
| Storage | `STORAGE_PROVIDER=firebase` |
| Project | `pcn-anpr` |
| Firestore location | `asia-south1` |
| Storage bucket | `pcn-anpr.firebasestorage.app` |

PostgreSQL / SQLAlchemy are **legacy only** (test fixtures). Dual-write / outbox architectures are retired.

## Auth model

```
Email/password → Firebase Identity Toolkit
  → Admin SDK verify_id_token
  → Firestore users/{uid} profile (role, organization_id, site_ids)
  → API access JWT + refresh token (refreshTokens collection)
  → existing PWA Authorization: Bearer flow
```

Passwords are **never** stored in Firestore. Profiles hold RBAC / tenancy only.

## Firestore collections

| Collection | Purpose |
| --- | --- |
| `organizations` | Tenants |
| `sites` | Sites + connectivity fields |
| `users` | Profiles keyed by Firebase uid |
| `gates` | Entry/exit gates |
| `gateways` | VPN / CPE devices (no device key plaintext) |
| `nvrs` | NVR inventory |
| `cameras` | Camera metadata (+ server-only encrypted RTSP fields) |
| `vehicles` | Plates / visit counters |
| `anprEvents` | Confirmed event metadata + storage keys |
| `vehicleVisits` | Visit matching |
| `edgeAgents` | Edge agent credentials (key hash) |
| `refreshTokens` | Rotating API refresh hashes |

Events store **metadata + storage keys only** — never image bytes.

## Storage object keys

```
{organizationId}/events/{eventId}/snapshot.jpg
{organizationId}/events/{eventId}/plate_crop.jpg
{organizationId}/events/{eventId}/vehicle_crop.jpg
```

No public URLs by default; access via backend / signed URLs when added later.

## Cost controls

See `backend/app/firebase/cost_controls.py`:

- Event list page size capped (≤100)
- Heartbeat write throttle 30s
- Dashboard recent limit 20
- No per-camera permanent Firestore listeners by default
- Only confirmed ANPR events become `anprEvents` documents

## Security

- Client Firestore/Storage writes denied (`firestore.rules` / `storage.rules`)
- Org/site scoped reads via `users/{uid}`
- Admin SDK for privileged writes
- Camera RTSP secrets Fernet-encrypted; omitted from API responses
- Gateway / edge device keys stored as bcrypt hashes only

## Local startup (no PostgreSQL)

```bash
cp .env.example .env
# set FIREBASE_CREDENTIALS_FILE
cd backend && pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

Missing Firebase Admin config → clear `ValidationAppError` (no silent PG fallback).

## Smoke test

```bash
cd backend
python scripts/firebase_first_smoke.py
```

Creates disposable `_smoke` / tagged docs, exercises Auth profile path, Firestore CRUD, Storage, then deletes smoke data.

## Legacy rollback (not recommended)

```
AUTH_PROVIDER=jwt
DATASTORE_PROVIDER=sqlalchemy
STORAGE_PROVIDER=local
DATABASE_URL=...
```

SQLAlchemy models under `app/models/` and Alembic history are retained for reference/tests only.
