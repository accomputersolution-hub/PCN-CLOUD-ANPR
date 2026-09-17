# Setup (Firebase-first)

Normal development and production **do not require PostgreSQL or Docker**.

## Prerequisites

- Python 3.12+
- Node.js 20+
- Firebase project access (`pcn-anpr`)
- Firebase Admin SDK service-account JSON (local path only — never commit)

## 1. Configure environment

```bash
cp .env.example .env
```

Required for backend Admin SDK:

```
AUTH_PROVIDER=firebase
DATASTORE_PROVIDER=firestore
STORAGE_PROVIDER=firebase
FIREBASE_PROJECT_ID=pcn-anpr
FIREBASE_STORAGE_BUCKET=pcn-anpr.firebasestorage.app
FIREBASE_CREDENTIALS_FILE=C:\path\to\serviceAccount.json
FIREBASE_API_KEY=...
FIREBASE_AUTH_DOMAIN=pcn-anpr.firebaseapp.com
FIREBASE_APP_ID=...
JWT_SECRET=<long random string>
CREDENTIALS_ENCRYPTION_KEY=<fernet key>
```

Generate secrets:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`DATABASE_URL` is unused when `DATASTORE_PROVIDER=firestore`.

## 2. Backend

```bash
cd backend
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

macOS / Linux:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Startup fails clearly if Firebase Admin credentials are missing (no silent fallback to PostgreSQL).

## 3. Bootstrap first operator (once)

1. Create a user in Firebase Console → Authentication (email/password).
2. Copy the user's **UID**.
3. Create Firestore document `users/{uid}`:

```json
{
  "email": "admin@example.com",
  "full_name": "Platform Admin",
  "role": "SUPER_ADMIN",
  "organization_id": null,
  "is_active": true,
  "site_ids": [],
  "auth_provider": "firebase",
  "firebase_uid": "<same-uid>"
}
```

4. Log in via `POST /api/v1/auth/login` with that email/password (Identity Toolkit + Firestore profile → API JWT).

## 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## 5. Deploy rules / indexes (when changed)

```bash
npx -y firebase-tools@latest --project pcn-anpr deploy --only firestore:rules,firestore:indexes,storage
```

## 6. Edge agent (optional)

See [EDGE_AGENT.md](EDGE_AGENT.md). Edge uses API keys (`X-Edge-Id` / `X-Edge-Key`); ANPR/RTSP pipelines are unchanged.

## Tests

```bash
# Backend (forces legacy sqlalchemy for most fixtures via conftest)
cd backend
pytest

# Edge agent
cd edge-agent
set PYTHONPATH=..\anpr-engine;%PYTHONPATH%
pytest

# Frontend
cd frontend
npm test -- --run
npm run build
```

## Legacy SQLAlchemy path (optional)

Only for historical tests or emergency rollback:

```
AUTH_PROVIDER=jwt
DATASTORE_PROVIDER=sqlalchemy
STORAGE_PROVIDER=local
DATABASE_URL=postgresql+asyncpg://pcn:pcn@localhost:5432/pcn_cloud
```

This is **not** the supported product runtime.
