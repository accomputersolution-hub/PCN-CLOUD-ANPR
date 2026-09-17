# PCN Cloud ANPR

Multi-tenant Automatic Number Plate Recognition platform for hotels, resorts, housing societies, parking areas, hospitals, offices and commercial properties.

**Brand:** PCN CLOUD  
**Product:** PCN Cloud ANPR

The browser is an operations PWA (manage, search, report, configure). Continuous CCTV ANPR does **not** run in the browser.

```
CCTV/NVR → RTSP/ONVIF → Edge Agent → ANPR Engine → Backend API → PostgreSQL → Web/PWA
```

If the site internet drops, the edge agent keeps capturing into a local SQLite queue and syncs when the link returns.

## Milestone 1 (this repository)

Working path:

**LOGIN → DASHBOARD → CAMERA MANAGEMENT → EVENTS → VEHICLE SEARCH → responsive PWA**

Mock ANPR events are fully wired so the product can be demonstrated before live cameras and model inference.

## Quick start (development)

```bash
# 1. Environment
cp .env.example .env

# 2. Database
docker compose up -d postgres

# 3. Backend (from backend/)
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. Frontend (from frontend/)
npm install
npm run dev
```

- PWA / UI: http://localhost:5173
- API: http://localhost:8000/api/v1
- OpenAPI: http://localhost:8000/api/v1/docs

All-in-one:

```bash
docker compose up -d --build
```

Then open http://localhost:8080

## Demo accounts

Password for all seeded users: `ChangeMe@12345`

| Email | Role |
| --- | --- |
| admin@pcncloud.in | Super Admin |
| orgadmin@pcncloud.in | Organization Admin (Hotel A) |
| manager@pcncloud.in | Site Manager (Lonavala) |
| guard@pcncloud.in | Security Guard |
| viewer@pcncloud.in | Viewer |
| societyadmin@pcncloud.in | Organization Admin (Society B) |

## Tests

```bash
# Backend
cd backend
pytest

# Frontend
cd frontend
npm test

# ANPR engine
cd anpr-engine
pytest

# Edge agent
cd edge-agent
pytest
```

## Docs

- [SETUP.md](SETUP.md) — exact commands
- [ARCHITECTURE.md](ARCHITECTURE.md) — choices and tenant model
- [API.md](API.md) — versioned REST + WebSocket
- [EDGE_AGENT.md](EDGE_AGENT.md) — site agent, queue, sync
- [ANPR_PIPELINE.md](ANPR_PIPELINE.md) — replaceable detectors/OCR

## License note

Default ANPR providers are in-process mocks (no copyleft model weights). PaddleOCR (Apache 2.0) can be plugged in later. Do not add a GPL detector as the default engine without a legal review.
