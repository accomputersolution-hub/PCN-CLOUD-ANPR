# Deployment

Customers keep their existing CCTV and NVR. They should not need to run a Windows PC unless they choose local ANPR.

## Example A — existing VPN router

```
CCTV cameras
    → existing NVR
        → MikroTik / compatible VPN router (already on site)
            → secure VPN
                → PCN Cloud (API, PostgreSQL, PWA)
```

Site settings:

- `connectivity_mode = EXISTING_VPN_ROUTER`
- `anpr_deployment_mode = LOCAL_EDGE_AGENT` if an Edge Agent still runs on-site
- Enroll the router as a gateway of type `EXISTING_VPN_ROUTER` (inventory + heartbeat; no vendor CLI in-app)

## Example B — PCN Cloud Gateway

```
CCTV cameras
    → existing NVR
        → customer's existing LAN router (no VPN)
            → PCN Cloud Gateway (ER605-class or other supported CPE)
                → secure outbound VPN
                    → PCN Cloud
```

Site settings:

- `connectivity_mode = PCN_CLOUD_GATEWAY`
- Enroll type `PCN_CLOUD_GATEWAY`
- Store vendor/model as metadata (`TP-Link` / `ER605` is an example, not a code branch)

## Local development (unchanged)

```bash
cp .env.example .env
docker compose up -d postgres
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm install
npm run dev
```

Edge Agent RTSP/ANPR workflow is unchanged (`EDGE_AGENT.md`). Gateway software is specification-only in this phase (`gateway-agent/`).

## Production notes

- Set `SEED_DEMO_DATA=false`, unique `JWT_SECRET`, unique `CREDENTIALS_ENCRYPTION_KEY`
- Do not publish Postgres `5432` to the internet
- Camera passwords stay Fernet-encrypted; gateway device keys are bcrypt hashes
- Confirmed ANPR snapshots stay on the storage backend (`STORAGE_PROVIDER=local` today; Firebase/S3 later)
