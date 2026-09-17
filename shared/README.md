Shared contracts live in:

- `backend/app/schemas/` — Pydantic (source of truth for the API)
- `frontend/src/shared/api/types.ts` — TypeScript mirrors

Keep those two in sync when changing fields. OpenAPI at `/api/v1/openapi.json` is generated from the backend.
