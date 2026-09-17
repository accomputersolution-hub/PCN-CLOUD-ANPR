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
  → Firebase (Auth + Firestore + Storage) — source of truth
  → Web / PWA (management, search, reports)
```

Offline: events stay in SQLite. When the WAN returns, `/api/v1/edge/sync` uploads by event ID (idempotent).

## Architectural choices (V1)

| Topic | Choice | Why |
| --- | --- | --- |
| API | FastAPI + Pydantic v2, `/api/v1` | OpenAPI for free, typed contracts |
| Datastore | Cloud Firestore (`DATASTORE_PROVIDER=firestore`) | Firebase-first; no PostgreSQL required at runtime |
| Auth | Firebase Authentication + Firestore user profiles + API JWT | PWA keeps Bearer tokens; passwords stay in Firebase Auth |
| Camera secrets | Fernet at rest on camera docs (Admin only); omitted from API responses | Operators never see stored RTSP passwords |
| Tenant isolation | `organization_id` + `site_ids` on profiles; `TenantContext` | Enforced in the API and Firestore rules |
| RBAC | Permission sets per role in `app/core/rbac.py` | Backend 403s even if a route is visible |
| Realtime | FastAPI WebSocket hub scoped by org | Dashboard without Firestore polling |
| Storage | Firebase Storage (`STORAGE_PROVIDER=firebase`) | Evidence blobs; no public URLs by default |
| Legacy SQL | `DATASTORE_PROVIDER=sqlalchemy` | Tests / emergency only — not product runtime |
| ANPR models | Interfaces + mock providers | No GPL default; PaddleOCR can replace OCR later |
| Dedup / visits | Site JSON settings | Configurable without schema migrations |
| Timezones | Per-site IANA TZ, default `Asia/Kolkata` | Events store UTC + local timestamp |
| PWA | vite-plugin-pwa, network-only for `/api` | Installable shell; ANPR is not in the browser |

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

## Firebase / cloud migration (pcn-anpr provisioned; app defaults unchanged)

| Concern | Env | Default |
| --- | --- | --- |
| Auth | `AUTH_PROVIDER` | `jwt` (optional `firebase`) |
| Datastore | `DATASTORE_PROVIDER` | `sqlalchemy` (optional `firestore`) |
| Storage | `STORAGE_PROVIDER` | `local` (optional `firebase`) |

Cloud project **`pcn-anpr`**: Firestore `(default)` in **`asia-south1`**, email/password Auth, Web app created, tenant rules deployed. Storage default bucket still needs Console **Get Started** once. Details: [FIREBASE_MIGRATION.md](FIREBASE_MIGRATION.md).

```
AuthProvider
  ├── JwtAuthProvider          (default)
  └── FirebaseAuthProvider     (Identity Toolkit + Admin verify + PG RBAC profile)

Datastore
  ├── SQLAlchemy repositories  (default)
  └── Firestore repositories   (Admin SDK; metadata only)

StorageBackend
  ├── LocalFilesystemStorage   (default)
  └── FirebaseStorage          (Admin SDK evidence blobs)
```

Do not delete PostgreSQL. ANPR ingest, visits, and the edge pipeline stay on the current stack until an explicit cutover.

## Explicitly not in V1 / Phase 6A

Billing, WhatsApp, PMS, facial recognition, barrier control. **Automatic live ENTRY/EXIT from RTSP OCR** is not enabled yet.
