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

CREATE TABLE IF NOT EXISTS god_mode_ops.write_control (
    id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    writes_enabled boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO god_mode_ops.write_control (id, writes_enabled)
VALUES (1, false)
ON CONFLICT (id) DO NOTHING;
