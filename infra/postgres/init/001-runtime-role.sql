-- Local/CI bootstrap only. Production infrastructure must provision a rotated login
-- and grant it this restricted role without committing credentials.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'creative_marketer_runtime') THEN
    CREATE ROLE creative_marketer_runtime LOGIN PASSWORD 'creative_marketer_runtime';
  END IF;
END
$$;

ALTER ROLE creative_marketer_runtime NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
