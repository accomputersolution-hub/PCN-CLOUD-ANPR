# API

Interactive docs (FastAPI):

- Swagger: http://localhost:8000/api/v1/docs
- ReDoc: http://localhost:8000/api/v1/redoc
- OpenAPI JSON: http://localhost:8000/api/v1/openapi.json

All application routes are versioned under `/api/v1/`.

## Auth

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/auth/login` | Returns access + refresh + user |
| POST | `/auth/refresh` | Rotates refresh token |
| POST | `/auth/logout` | Revokes refresh token |
| GET | `/auth/me` | Current user |
| GET | `/auth/permissions` | Role permission list |

Header: `Authorization: Bearer <access_token>`

## Core

| Method | Path |
| --- | --- |
| GET | `/health` |
| GET | `/health/system` (super admin) |
| GET | `/dashboard/summary` |
| GET/POST/PATCH | `/organizations` |
| GET/POST/PATCH | `/sites` |
| GET/POST/PATCH/DELETE | `/gates` |
| GET/POST/PATCH/DELETE | `/cameras` |
| GET | `/cameras/status` |
| POST | `/cameras/test` |
| POST | `/cameras/{id}/enable` |
| GET | `/events` |
| GET | `/events/{id}` |
| POST | `/events/{id}/correct` |
| POST | `/events/{id}/classify` |
| DELETE | `/events/{id}` |
| GET | `/vehicles` |
| GET | `/vehicles/{plate}` |
| PATCH | `/vehicles/{plate}/visitor` |
| POST | `/visits/{id}/resolve` |
| GET | `/reports/summary` |
| GET | `/reports/export` (CSV) |
| GET | `/audit` |
| POST | `/retention/run` |

Event filters: `plate`, `date_from`, `date_to`, `direction`, `camera_id`, `gate_id`, `site_id`, `organization_id`.

Camera GET responses never include RTSP URLs or passwords. Flags: `credentials_configured`, `rtsp_configured`.

## Mock ANPR

| Method | Path |
| --- | --- |
| POST | `/mock/events` |
| POST | `/mock/upload` | multipart image/video + `plate_text` + `camera_id` |

## Edge

| Method | Path | Auth |
| --- | --- | --- |
| POST | `/edge/register` | site id + agent key |
| POST | `/edge/heartbeat` | `X-Edge-Id`, `X-Edge-Key` |
| POST | `/edge/events` | same |
| POST | `/edge/sync` | same (alias of events ingest) |

Sync body is a list of events with client-generated `id` values. Replays return the same IDs.

## WebSocket

```
ws://localhost:8000/api/v1/ws?token=<access_token>
```

Messages:

```json
{ "type": "anpr.event", "payload": { "id": "...", "plate": "MH12AB1234" } }
{ "type": "camera.status", "payload": { "id": "...", "status": "ONLINE" } }
{ "type": "edge.status", "payload": { "id": "...", "status": "CONNECTED" } }
{ "type": "sync.status", "payload": { "agent_id": "...", "accepted": 3 } }
```

In local Vite, the frontend uses `ws://localhost:5173/api/v1/ws` which is proxied.

## Errors

```json
{ "code": "forbidden", "message": "Insufficient permissions", "details": {} }
```
