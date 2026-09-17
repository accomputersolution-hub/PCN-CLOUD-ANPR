# Setup

## Prerequisites

- Python 3.12+
- Node.js 20+
- Docker (for PostgreSQL; optional Redis)
- Git

## 1. Clone and configure

```bash
cp .env.example .env
```

Generate production secrets before any real deployment:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `CREDENTIALS_ENCRYPTION_KEY` and a long random `JWT_SECRET`. Never commit `.env`.

### Providers (defaults — keep for local)

```
AUTH_PROVIDER=jwt
DATASTORE_PROVIDER=sqlalchemy
STORAGE_PROVIDER=local
```

Firebase variables (`FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, `FIREBASE_CREDENTIALS_FILE`, web client keys) may be left empty for local mode.

**Phase 2 — optional Firebase Storage only** (events still PostgreSQL):

```bash
cd backend
pip install -r requirements-firebase.txt
```

Then set `STORAGE_PROVIDER=firebase` plus project id, bucket, and credentials file. Keep `AUTH_PROVIDER=jwt` and `DATASTORE_PROVIDER=sqlalchemy`. See [FIREBASE_MIGRATION.md](FIREBASE_MIGRATION.md).

Setting `AUTH_PROVIDER=firebase` or `DATASTORE_PROVIDER=firestore` without a complete wiring still fails clearly — it does not return mock data.

## 2. Start PostgreSQL

```bash
docker compose up -d postgres
```

Connection used by the backend:

```
postgresql+asyncpg://pcn:pcn@localhost:5432/pcn_cloud
```

## 3. Backend

```bash
cd backend
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

macOS / Linux:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

On first start with `SEED_DEMO_DATA=true`, demo organizations, cameras and mock events are created.

### Migration command

```bash
cd backend
alembic upgrade head
```

Connectivity / gateway tables are in revision `0004_connectivity_gateways`.

Create a new revision (after model changes):

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## 4. Frontend PWA

```bash
cd frontend
npm install
npm run dev
```

Development URL: **http://localhost:5173**

Vite proxies `/api` to `http://localhost:8000`.

Production build:

```bash
cd frontend
npm run build
npm run preview
```

## 5. Full Docker stack

```bash
docker compose up -d --build
```

- UI: http://localhost:8080
- API: http://localhost:8000/api/v1/docs

Optional Redis:

```bash
docker compose --profile full up -d
```

V1 rate limits use in-memory storage if `REDIS_URL` is empty.

## 6. Tests

```bash
cd backend
pytest
```

```bash
cd frontend
npm test
```

```bash
cd anpr-engine
pip install -e .
pytest
```

```bash
cd edge-agent
pip install -r requirements.txt
pytest
```

## 7. Edge agent

### Mock ANPR (no camera)

```bash
cd edge-agent
pip install -r requirements.txt
set PYTHONPATH=..\anpr-engine;%PYTHONPATH%
set EDGE_MOCK_MODE=true
set EDGE_MOCK_ONESHOT=true
python -m pcn_edge.main
```

### Live RTSP (Phase 5)

1. Install **FFmpeg** and ensure `ffmpeg` is on PATH.
2. Register an edge agent (`POST /api/v1/edge/register`) and put `EDGE_AGENT_ID` / `EDGE_AGENT_KEY` in `.env`.
3. Add a camera with RTSP URL (+ username/password) in the Cameras UI. Credentials are encrypted; the UI never shows them again.
4. Click **Test RTSP**, then **Start**.
5. Run the agent:

```bash
set EDGE_MOCK_MODE=false
set EDGE_RTSP_ENABLED=true
set EDGE_FRAME_INTERVAL=0.5
set EDGE_FRAME_SAVE_DIR=./data/edge-frames
set DEBUG_SAVE_ALL_FRAMES=false
python -m pcn_edge.main
```

Detection snapshots (vehicle/plate/OCR hits) appear under `EDGE_FRAME_SAVE_DIR`. Camera health (status, FPS, last frame, errors) updates via heartbeat.

**Live ANPR events (Phase 6B):** set `ANPR_LIVE_ENABLED=true` (with `EDGE_MOCK_MODE=false`). Sampled frames go to a bounded inference worker; plates are confirmed over multiple observations, then one ENTRY/EXIT event is enqueued (cooldown suppresses duplicates). Use Mock ANPR when you do not want live camera events.

### First CP Plus / Dahua / Hikvision NVR

| Brand | Typical RTSP pattern |
| --- | --- |
| Hikvision | `rtsp://<nvr-ip>:554/Streaming/Channels/<channel>01` (101 = ch1 main) |
| Dahua / CP Plus | `rtsp://<nvr-ip>:554/cam/realmonitor?channel=1&subtype=0` |

Enter host URL without password in **RTSP URL**, and put username/password in the separate fields. Prefer a LAN IP reachable from the PC running the edge agent.

## 8. ANPR image inference (Phase 6A)

Does **not** create live ENTRY/EXIT events. Processes saved JPEGs only.

```powershell
cd anpr-engine
..\backend\.venv\Scripts\pip.exe install -r requirements.txt
# Optional real OCR (Apache 2.0):
..\backend\.venv\Scripts\pip.exe install -r requirements-ocr.txt

$env:PYTHONPATH = "$PWD"
python -m anpr_engine.cli --image "..\edge-agent\data\edge-frames\<your-frame>.jpg"
python -m anpr_engine.cli --folder "..\edge-agent\data\edge-frames"
```

Outputs: console report, `output/results.json`, `output/annotated_*.jpg`.

Dev API (authenticated): `POST /api/v1/anpr/test-image` with multipart file upload.

## 9. SQLite fallback (no Docker)

For quick API experiments only:

```
DATABASE_URL=sqlite+aiosqlite:///./data/pcn.db
```

PostgreSQL remains the supported production database.
