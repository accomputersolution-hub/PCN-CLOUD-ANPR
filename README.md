# PCN Cloud ANPR

Multi-tenant Automatic Number Plate Recognition platform for hotels, resorts, housing societies, parking areas, hospitals, offices and commercial properties.

**Brand:** PCN CLOUD  
**Product:** PCN Cloud ANPR

The browser is an operations PWA (manage, search, report, configure). Continuous CCTV ANPR does **not** run in the browser.

```
CCTV/NVR → RTSP/ONVIF → Edge Agent → ANPR Engine → Backend API → Firebase (Auth + Firestore + Storage) → Web/PWA
```

**Firebase-first runtime (project `pcn-anpr`):**

| Concern | Default |
| --- | --- |
| Auth | `AUTH_PROVIDER=firebase` |
| Datastore | `DATASTORE_PROVIDER=firestore` |
| Storage | `STORAGE_PROVIDER=firebase` |

PostgreSQL is **not** required for normal development or production. Legacy SQLAlchemy remains available only for tests via explicit `DATASTORE_PROVIDER=sqlalchemy`.

If the site internet drops, the edge agent keeps capturing into a local SQLite queue and syncs when the link returns.

See [CONNECTIVITY.md](CONNECTIVITY.md), [GATEWAY.md](GATEWAY.md), [SETUP.md](SETUP.md), [FIREBASE_MIGRATION.md](FIREBASE_MIGRATION.md).

## Milestone 1

**LOGIN → DASHBOARD → CAMERA MANAGEMENT → EVENTS → VEHICLE SEARCH → responsive PWA**

## Quick start (development — no PostgreSQL)

```bash
# 1. Environment
cp .env.example .env
# Set FIREBASE_CREDENTIALS_FILE to your Admin SDK JSON (never commit it)
# Confirm FIREBASE_PROJECT_ID=pcn-anpr and FIREBASE_* web keys

# 2. Backend (from backend/)
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 3. Frontend (from frontend/)
npm install
npm run dev
```

- PWA / UI: http://localhost:5173
- API: http://localhost:8000/api/v1
- OpenAPI: http://localhost:8000/api/v1/docs

Create a Firebase Auth user, then a matching Firestore `users/{uid}` profile document (role, organization_id, site_ids). See [SETUP.md](SETUP.md).

## Docs

| Doc | Contents |
| --- | --- |
| [SETUP.md](SETUP.md) | Local Firebase-first setup |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design |
| [FIREBASE_MIGRATION.md](FIREBASE_MIGRATION.md) | Firebase ops & collections |
| [API.md](API.md) | HTTP API |
| [EDGE_AGENT.md](EDGE_AGENT.md) | Site agent |
| [CONNECTIVITY.md](CONNECTIVITY.md) | VPN / gateway modes |

## License

Proprietary — PCN Cloud.
