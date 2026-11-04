-- Staging prerequisite for the Lee County negative write-guard integration test.
-- The workflow applies this only through the staging-pinned pooler connection.

CREATE SCHEMA IF NOT EXISTS god_mode_ops;

CREATE OR REPLACE FUNCTION god_mode_ops.require_db_writes_enabled()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF current_setting('god_mode.writes_enabled', true) IS DISTINCT FROM '1' THEN
        RAISE EXCEPTION 'Database writes are currently disabled'
            USING ERRCODE = '55000';
    END IF;
END;
$$;
