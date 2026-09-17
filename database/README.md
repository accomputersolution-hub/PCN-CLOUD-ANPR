# Database

Alembic migrations live in `backend/alembic/`.

PostgreSQL init scripts for Docker are in `database/init/`.

Production indexes (created in `0001_initial`):

- organization_id, site_id, camera_id, plate_normalized, timestamp on `anpr_events`
- organization_id + plate_normalized unique on `vehicles`
