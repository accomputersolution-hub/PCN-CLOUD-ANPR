-- Optional extra init for local PostgreSQL containers.
-- The application user/database are created from POSTGRES_* env vars.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
