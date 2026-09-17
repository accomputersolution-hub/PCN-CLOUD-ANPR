# Architecture

PCN Cloud ANPR is a multi-tenant operations platform. Continuous video analytics run at the customer site. The PWA is a client for people, not for CCTV decode.

## Data path

```
CCTV / NVR
  → RTSP / ONVIF
  → Edge Agent (site)
      → frame capture
      → ANPR engine (replaceable)
      → local snapshots
      → SQLite outbox
  → Backend API (when online)
  → PostgreSQL
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
| Storage | `StorageBackend` + local filesystem | S3-compatible is an extension point, not stubbed as “done” |
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
      → Site (timezone, retention inherited from org, ANPR thresholds in settings)
          → Gate (ENTRY / EXIT / MIXED)
              → Camera (ENTRY / EXIT / BOTH)
          → Edge agent
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

## Explicitly not in V1 / Phase 6A

Billing, WhatsApp, PMS, facial recognition, barrier control. **Automatic live ENTRY/EXIT from RTSP OCR** is not enabled yet.
