# Firebase Migration

This document describes how PCN Cloud ANPR moves toward Firebase **without** deleting PostgreSQL or rewriting the ANPR pipeline.

## Provider modes

**Default (local / current production path):**

| Concern | Provider |
| --- | --- |
| Auth | `AUTH_PROVIDER=jwt` (PostgreSQL users + JWT) |
| Datastore | `DATASTORE_PROVIDER=sqlalchemy` (PostgreSQL) |
| Object storage | `STORAGE_PROVIDER=local` |

**Phase 2 optional (Storage only):**

| Concern | Provider |
| --- | --- |
| Auth | `jwt` (unchanged) |
| Datastore | `sqlalchemy` (events/visits stay in PostgreSQL) |
| Object storage | `STORAGE_PROVIDER=firebase` |

Requires: `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, `FIREBASE_CREDENTIALS_FILE` (or `_JSON`), and `pip install -r requirements-firebase.txt`.

**Later (not enabled):**

| Concern | Provider |
| --- | --- |
| Auth | `AUTH_PROVIDER=firebase` |
| Datastore | `DATASTORE_PROVIDER=firestore` |

Missing configuration fails clearly. There is **no** fake success path.

## Entity mapping (PostgreSQL → Firestore)

| PostgreSQL / domain | Firestore collection (planned) | Notes |
| --- | --- | --- |
| `organizations` | `organizations/{orgId}` | Top-level tenant |
| `sites` | `sites/{siteId}` | Always includes `organization_id` |
| `users` | `users/{userId}` | Profile + role; link `firebase_uid` when Auth moves |
| `gateways` | `gateways/{gatewayId}` | Connectivity device metadata only |
| `nvrs` | `nvrs/{nvrId}` | LAN inventory |
| `cameras` | `cameras/{cameraId}` | No RTSP passwords in client-readable docs |
| `vehicles` | `vehicles/{vehicleId}` | Plate index per org |
| `anpr_events` | `anprEvents/{eventId}` | Metadata + storage keys only |
| `snapshots` | *(object storage)* | JPEG under `{orgId}/events/{eventId}/…` |
| `vehicle_visits` | `vehicleVisits/{visitId}` (later) | Keep visit logic in services |
| `refresh_tokens` | N/A with Firebase Auth | Session managed by Firebase |
| `audit_logs` | `auditLogs/{id}` or retain PG | Prefer append-only |

Do **not** mirror every SQL table 1:1. Prefer domain records in `app/domain/` over dumping ORM rows.

## Evidence path (confirmed events only)

```
ANPR Edge confirms plate
  → event metadata → PostgreSQL (Phase 2; Firestore later)
  → snapshot + plate crop → Local or Firebase Storage
```

Object keys (Phase 2):

```
{organizationId}/events/{eventId}/snapshot.jpg
{organizationId}/events/{eventId}/plate_crop.jpg
{organizationId}/events/{eventId}/vehicle_crop.jpg
```

- Never store image bytes inside Firestore documents.
- Debug frames stay on the edge agent disk.
- UI still loads evidence through authenticated `GET /api/v1/storage/{key}` (Admin SDK download on the server).

## Cost-control rules

See `app/firebase/cost_controls.py`:

- Event lists: default page size 50, max 100
- Camera/gateway heartbeat writes: minimum 30s interval
- Dashboard: limited recent events (20)
- No permanent per-camera Firestore listeners by default

## Migration phases

1. **Phase 1 (done)** — config, AuthProvider, domain records, Firestore repo stubs, provider selection, docs, unit tests.
2. **Phase 2 (done)** — Firebase Admin SDK Storage adapter for confirmed ANPR evidence; events remain in PostgreSQL. Default remains `STORAGE_PROVIDER=local`.
3. **Phase 3** — Dual-write selected collections (orgs/sites/gateways) to Firestore behind feature flags; read path still PostgreSQL.
4. **Phase 4** — Firebase Authentication with profile documents; JWT remains fallback until cutover.
5. **Phase 5** — Event/vehicle read path on Firestore with pagination; PostgreSQL retained for rollback.
6. **Phase 6** — Optional decommission of unused PG tables after verification (not automatic).

## Enabling Phase 2 Storage

```bash
cd backend
pip install -r requirements-firebase.txt
```

In `.env` (never commit real keys):

```
STORAGE_PROVIDER=firebase
FIREBASE_PROJECT_ID=your-project
FIREBASE_STORAGE_BUCKET=your-project.appspot.com
FIREBASE_CREDENTIALS_FILE=/secure/path/service-account.json
# Keep:
AUTH_PROVIDER=jwt
DATASTORE_PROVIDER=sqlalchemy
```

Deploy `storage.rules.example` only after review. Prefer denying client writes; backend uses Admin SDK.

## Risks and limitations

- Firestore is not a relational DB; visit matching and reporting SQL stay on PG longer.
- Phase 2 does not migrate existing local files automatically — re-ingest or copy objects if needed.
- Admin SDK credentials must never ship to the PWA.
- Live Firebase project + billing + reviewed rules are required before enabling `STORAGE_PROVIDER=firebase` in production.

## Rollback

1. Set `STORAGE_PROVIDER=local` (and keep `AUTH_PROVIDER=jwt`, `DATASTORE_PROVIDER=sqlalchemy`).
2. Restart API processes.
3. PostgreSQL remains the source of truth for events/visits.

## Security rules status

See `firestore.rules.example` and `storage.rules.example`. Starting structures only — **not** production-verified.
