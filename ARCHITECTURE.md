# Architecture

PCN Cloud ANPR is a multi-tenant operations platform. Continuous video analytics run at the customer site. The PWA is a client for people, not for CCTV decode.

## Data path

```
CCTV / NVR
  → private LAN
  → connectivity path (existing VPN router OR PCN Cloud Gateway)
  → RTSP / ONVIF (never published as public :554 by default)
  → Edge Agent (site) — ANPR
      → frame capture
      → ANPR engine (replaceable)
      → confirmed snapshots only
      → SQLite outbox
  → Backend API (when online)
  → PostgreSQL (Firestore adapter planned behind repositories)
  → Web / PWA (management, search, reports)
```

Offline: events stay in SQLite. When the WAN returns, `/api/v1/edge/sync` uploads by event ID (idempotent).

## Architectural choices (V1)

| Topic | Choice | Why |
| --- | --- | --- |
| API | FastAPI + Pydantic v2, `/api/v1` | OpenAPI for free, typed contracts |
| DB | PostgreSQL 16 + SQLAlchemy 2 async | Production default; SQLite only for tests/dev fallback |
| Auth | Access JWT (15 min) + rotating refresh tokens hashed at rest | Simple, works for PWA; httpOnly cookies are a later hardening step |
| Passwords | bcrypt | No plaintext |
| Camera secrets | Fernet at rest; omitted from API responses | Operators never see stored RTSP passwords |
| Tenant isolation | `organization_id` on tenant tables + query filters in `TenantContext` | Enforced in the API, not only the UI |
| RBAC | Permission sets per role in `app/core/rbac.py` | Backend 403s even if a route is visible |
| Realtime | FastAPI WebSocket hub scoped by org (super admin sees all) | Dashboard without polling |
| Storage | `StorageBackend` + local filesystem | Phase 2: `STORAGE_PROVIDER=firebase` uses Admin SDK for evidence blobs; default remains local |
| Datastore | SQLAlchemy today (`DATASTORE_PROVIDER=sqlalchemy`) | Gateway/NVR/site connectivity + domain records go through `app/repositories` so Firestore can replace PG without rewriting ANPR |
| Auth providers | `AUTH_PROVIDER=jwt` (live) | `AuthProvider` abstraction; Firebase Auth adapter refuses unconfigured / unwired use |
| ANPR models | Interfaces `VehicleDetector`, `PlateDetector`, `OCRProvider` + mock providers | No GPL default; PaddleOCR (Apache 2.0) can replace OCR later |
| Dedup / visits | Site JSON settings: min confidence, duplicate window, cooldown | Configurable without a new table |
| Timezones | Per-site IANA TZ, default `Asia/Kolkata` | Events store UTC + local timestamp |
| Rate limit | slowapi in-memory; Redis optional | Avoid a hard Redis dependency in V1 |
| PWA | vite-plugin-pwa, network-only for `/api` | Installable shell; ANPR is not claimed in the browser |
| Frontend tokens | memory + localStorage | Required for PWA restore; trade-off vs httpOnly cookies documented here |

## Tenancy

```
Platform
  → Organization
      → Site (timezone, ANPR thresholds, connectivity_mode, anpr_deployment_mode)
          → Gate (ENTRY / EXIT / MIXED)
              → Camera (IP / NVR channel / RTSP / ONVIF)
          → NVR (optional)
          → Gateway (existing VPN router or PCN Cloud Gateway)
          → Edge agent (ANPR — separate from gateway)
```

Super Admin has no `organization_id` and may query all tenants. Every other role is constrained to its organization. Site Manager / Guard / Viewer may be further limited via `user_site_access`.

## Roles

| Role | Can |
| --- | --- |
| Super Admin | Platform, orgs, users, health, retention job |
| Org Admin | Sites, gates, cameras, users in org, reports, mock events |
| Site Manager | Cameras, gates, reports, classify, resolve unmatched visits |
| Security Guard | Live status, recent events, search, classify, visitor notes |
| Viewer | Read dashboard, events, vehicles, cameras |

## Visit matching

- ENTRY with no open visit → `CURRENTLY_INSIDE`
- EXIT matching an open visit → `COMPLETED` + duration
- EXIT with no open visit → `EXIT_WITHOUT_MATCH` (no invented entry)
- Managers may set `MANUALLY_RESOLVED`

## Duplicate sessions

Same normalized plate + same camera/gate within `duplicate_window_seconds` / `event_cooldown_seconds` does not insert another event. Edge retries use the same event UUID so sync is idempotent.

## Privacy

`organizations.retention_days` (7 / 30 / 90 / 180 / custom). Super Admin can run `POST /api/v1/retention/run`. Automatic scheduler is a later ops concern.

## Live RTSP (Phase 5) + image ANPR (Phase 6A)

```
CCTV / NVR → RTSP → Edge Agent (FFmpeg) → JPEG frames
                                         ↓
                              ANPR engine (CLI / dev API)
                              vehicle → plate → OCR → normalize
                              (no auto DB events in 6A)
```

- Capture runs only in `edge-agent/`. The PWA never opens RTSP sockets.
- Phase 6A adds **offline/image** ANPR via `python -m anpr_engine.cli` and `POST /api/v1/anpr/test-image`.
- Mock ANPR page remains for creating events without cameras.
- Detectors default to OpenCV heuristics + PaddleOCR (Apache 2.0). No AGPL YOLO by default.

## Connectivity vs ANPR

The Edge Agent is unchanged: RTSP, FFmpeg, PaddleOCR, temporal confirmation, cooldown. Gateways only describe **how the cloud reaches the NVR LAN**. See CONNECTIVITY.md and GATEWAY.md.

## Firebase / cloud migration (Phase 1 — prepared, not switched)

Default providers (keep these for local/dev):

| Concern | Env | Default |
| --- | --- | --- |
| Auth | `AUTH_PROVIDER` | `jwt` |
| Datastore | `DATASTORE_PROVIDER` | `sqlalchemy` (alias: `postgres`) |
| Storage | `STORAGE_PROVIDER` | `local` |

Abstractions:

```
AuthProvider
  ├── JwtAuthProvider          (live)
  └── FirebaseAuthProvider     (config-checked; not enabled)

Datastore
  ├── SQLAlchemy repositories  (live)
  └── Firestore repositories   (structure only; raises until wired)

StorageBackend
  ├── LocalFilesystemStorage   (default / live)
  └── FirebaseStorage          (Phase 2 Admin SDK — set STORAGE_PROVIDER=firebase)
```

- Domain records: `app/domain/connectivity.py`, `app/domain/records.py`
- Provider helpers: `app/core/providers.py`, `app/auth/`, `app/firebase/`
- Cost controls: pagination caps, heartbeat throttle helpers (`app/firebase/cost_controls.py`)
- Example rules: `firestore.rules.example`, `storage.rules.example` (not production-verified)
- Migration plan: [FIREBASE_MIGRATION.md](FIREBASE_MIGRATION.md)

Phase 2: confirmed ANPR snapshots/crops can land in Firebase Storage while **event rows stay in PostgreSQL**. Install `backend/requirements-firebase.txt` only when enabling Firebase Storage. Default remains local disk.

IDs remain string UUIDs; every tenant document/row keeps `organization_id` / `site_id`.  
ANPR evidence = metadata in DB + object-storage keys (never image bytes in Firestore).  
Do not delete PostgreSQL. ANPR ingest, visits, and the edge pipeline stay on the current stack.

## Explicitly not in V1 / Phase 6A

Billing, WhatsApp, PMS, facial recognition, barrier control. **Automatic live ENTRY/EXIT from RTSP OCR** is not enabled yet.
